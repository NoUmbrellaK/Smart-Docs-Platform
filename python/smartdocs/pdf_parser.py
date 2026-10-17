"""Linux PDF extraction in a bounded, credential-free child process.

This is resource containment, not a filesystem/network security sandbox.
Run the future worker service as an unprivileged account; authorize reads in C++.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from .text_parser import Chunk, Limits, ParseError, _validate_input, parse_text


PARSER_REVISION = "pypdf-6.21.0-lines-v1"
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
WORKER_ERRORS = frozenset({"invalid_pdf", "encrypted_pdf", "too_many_pages",
                          "page_stream_too_large", "text_too_large", "input_too_large",
                          "pdf_resource_limit", "pdf_dependency_unavailable"})


@dataclass(frozen=True)
class PdfLimits(Limits):
    max_pages: int = 300
    max_stream_bytes: int = 16 * 1024 * 1024
    timeout_seconds: float = 15.0

    def __post_init__(self):
        super().__post_init__()
        if (self.max_bytes > 8 * 1024 * 1024 or self.max_chars > 2_000_000
                or self.max_chunks > 2_000
                or type(self.max_pages) is not int or not 1 <= self.max_pages <= 300
                or type(self.max_stream_bytes) is not int
                or not 1 <= self.max_stream_bytes <= MAX_OUTPUT_BYTES
                or type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds)
                or not 0 < self.timeout_seconds <= 15):
            raise ParseError("invalid_input")


@dataclass(frozen=True)
class PdfChunk(Chunk):
    page: int


@dataclass(frozen=True)
class PdfDocument:
    chunks: tuple[PdfChunk, ...]
    page_count: int
    pages_without_text: tuple[int, ...]


def _extract_pages(data: bytes, limits: PdfLimits) -> list[str]:
    worker = Path(__file__).with_name("pdf_worker.py")
    command = [sys.executable, "-I", "-B", str(worker), json.dumps(asdict(limits))]
    with tempfile.TemporaryFile() as output:
        try:
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output,
                                  stderr=subprocess.DEVNULL, env={}, close_fds=True,
                                  start_new_session=True, cwd="/") as child:
                try:
                    child.communicate(input=data, timeout=limits.timeout_seconds)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.communicate()
                    raise ParseError("pdf_timeout") from None
                if child.returncode in (-signal.SIGKILL, -signal.SIGXCPU, -signal.SIGXFSZ):
                    raise ParseError("pdf_resource_limit")
                if child.returncode != 0:
                    raise ParseError("pdf_worker_failed")
        except OSError:
            raise ParseError("pdf_worker_failed") from None
        output.seek(0)
        raw = output.read(MAX_OUTPUT_BYTES + 1)
    if len(raw) > MAX_OUTPUT_BYTES:
        raise ParseError("pdf_resource_limit")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ParseError("pdf_worker_failed") from None
    if not isinstance(result, dict):
        raise ParseError("pdf_worker_failed")
    if "error" in result:
        code = result["error"]
        raise ParseError(code if isinstance(code, str) and code in WORKER_ERRORS
                         else "pdf_worker_failed")
    pages = result.get("pages")
    if (not isinstance(pages, list) or not pages or len(pages) > limits.max_pages
            or any(not isinstance(page, str) for page in pages)):
        raise ParseError("pdf_worker_failed")
    if sum(map(len, pages)) > limits.max_chars:
        raise ParseError("text_too_large")
    return pages


def parse_pdf(data: bytes, *, file_id: str, version_id: str,
              limits: PdfLimits = PdfLimits()) -> PdfDocument:
    """Extract all readable text, with explicit empty-page coverage, or fail."""
    if not isinstance(limits, PdfLimits):
        raise ParseError("invalid_input")
    _validate_input(data, file_id, version_id, limits)
    if not data.startswith(b"%PDF-"):
        raise ParseError("invalid_pdf")
    pages = _extract_pages(data, limits)
    source_hash = sha256(data).hexdigest()
    chunks = []
    empty_pages = []
    text_limits = Limits(max_bytes=4 * limits.max_chars, max_chars=limits.max_chars,
                         chunk_chars=limits.chunk_chars, max_chunks=limits.max_chunks)
    for page_number, text in enumerate(pages, 1):
        if not text.strip():
            empty_pages.append(page_number)
            continue
        try:
            encoded_text = text.encode("utf-8")
        except UnicodeError:
            raise ParseError("invalid_pdf") from None
        page_chunks = parse_text(encoded_text, file_id=file_id,
                                 version_id=version_id, kind="txt", limits=text_limits)
        if len(chunks) + len(page_chunks) > limits.max_chunks:
            raise ParseError("too_many_chunks")
        for chunk in page_chunks:
            identity = json.dumps([PARSER_REVISION, file_id, version_id, source_hash,
                                   page_number, chunk.char_start, chunk.char_end],
                                  ensure_ascii=True, separators=(",", ":")).encode("ascii")
            values = asdict(chunk)
            values.update(chunk_id=sha256(identity).hexdigest(), source_sha256=source_hash,
                          parser_revision=PARSER_REVISION, page=page_number)
            chunks.append(PdfChunk(**values))
    if not chunks:
        raise ParseError("no_extractable_text")
    return PdfDocument(tuple(chunks), len(pages), tuple(empty_pages))
