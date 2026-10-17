"""Bounded UTF-8 TXT/Markdown parsing with version-bound source locations.

No file access, authorization, publication, HTML rendering, or remote calls.
The caller must authorize and bound the read before supplying immutable bytes.
Offsets refer to BOM-free, LF-normalized Unicode code points, not source bytes.
"""

from dataclasses import dataclass
from hashlib import sha256
import json


PARSER_REVISION = "utf8-lines-v1"


class ParseError(ValueError):
    """Safe public error code: never include document contents in the message."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 8 * 1024 * 1024
    max_chars: int = 2_000_000
    chunk_chars: int = 1_200
    max_chunks: int = 2_000

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in
               (self.max_bytes, self.max_chars, self.chunk_chars, self.max_chunks)):
            raise ParseError("invalid_input")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    file_id: str
    version_id: str
    source_sha256: str
    parser_revision: str
    text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int


def _validate_input(data: bytes, file_id: str, version_id: str, limits: Limits):
    """Shared caller boundary for local text and PDF parsing."""
    if not isinstance(data, bytes) or not isinstance(limits, Limits):
        raise ParseError("invalid_input")
    for identifier in (file_id, version_id):
        if (not isinstance(identifier, str) or not identifier.strip()
                or len(identifier) > 128
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in identifier)):
            raise ParseError("invalid_input")
    if len(data) > limits.max_bytes:
        raise ParseError("input_too_large")


def parse_text(data: bytes, *, file_id: str, version_id: str, kind: str,
               limits: Limits = Limits()) -> list[Chunk]:
    """Return all chunks or raise ParseError; never return truncated success."""
    _validate_input(data, file_id, version_id, limits)
    if kind not in ("txt", "md"):
        raise ParseError("unsupported_format")
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise ParseError("invalid_encoding") from None
    if len(text) > limits.max_chars:
        raise ParseError("text_too_large")
    if any((ord(c) < 32 and c not in "\n\t") or 127 <= ord(c) <= 159 for c in text):
        raise ParseError("binary_text")
    if not text.strip():
        raise ParseError("empty_text")
    if (len(text) + limits.chunk_chars - 1) // limits.chunk_chars > limits.max_chunks:
        raise ParseError("too_many_chunks")

    source_hash = sha256(data).hexdigest()
    chunks = []
    line_start = 1
    for start in range(0, len(text), limits.chunk_chars):
        end = min(start + limits.chunk_chars, len(text))
        part = text[start:end]
        identity = json.dumps([PARSER_REVISION, file_id, version_id, source_hash, start, end],
                              ensure_ascii=True, separators=(",", ":")).encode("ascii")
        chunks.append(Chunk(
            chunk_id=sha256(identity).hexdigest(), file_id=file_id, version_id=version_id,
            source_sha256=source_hash, parser_revision=PARSER_REVISION, text=part,
            char_start=start, char_end=end, line_start=line_start,
            line_end=line_start + part[:-1].count("\n")))
        line_start += part.count("\n")
    return chunks
