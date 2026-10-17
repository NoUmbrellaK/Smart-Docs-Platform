"""Optional bounded local encoder; NOT an authorization endpoint or network sandbox.

Call only with authorized chunks and trusted deployment paths. Run in a separate
unprivileged, network-isolated service, not inside the 512 MiB parser worker.
"""

from hashlib import sha256
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import tempfile
from time import monotonic


MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_HASHES = {
    "model_optimized.onnx": "634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99",
    "tokenizer.json": "fa685fc160bbdbab64058d4fc91b60e62d207e8dc60b9af5c002c5ab946ded00",
    "config.json": "c8ec081fdad2df991bf5abbf18418fec7a5cdaa421f60ffb060a30040b8c376f",
    "tokenizer_config.json": "0666eebf692422757e1dddf3c9fb1ded73ba3dc726c5828671fc89e45bf3609f",
    "special_tokens_map.json": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
}
VERSIONS = {"fastembed": "0.9.0", "onnxruntime": "1.23.2", "numpy": "2.2.6",
            "tokenizers": "0.23.3", "huggingface-hub": "1.33.0"}
EMBEDDING_REVISION = "minilm-634d0f66-fastembed0.9.0-ort1.23.2-halves128-l2-v1"
MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_STAGE_BYTES = 256 * 1024
CHILD_ERRORS = frozenset({"embedding_invalid_input", "embedding_window_limit",
    "embedding_model_unavailable", "embedding_dependency_unavailable",
    "embedding_resource_limit", "embedding_invalid_result", "embedding_worker_failed",
    "embedding_must_be_unprivileged"})
ENCODING_ERRORS = CHILD_ERRORS | {"embedding_timeout", "embedding_result_too_large",
                                 "embedding_busy", "embedding_lock_unavailable"}


class EmbeddingError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_chunks(chunks):
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= 8:
        raise EmbeddingError("embedding_invalid_input")
    identities = set()
    for chunk in chunks:
        if not isinstance(chunk, dict) or set(chunk) != {"chunk_id", "text"}:
            raise EmbeddingError("embedding_invalid_input")
        identity, text = chunk["chunk_id"], chunk["text"]
        if (not isinstance(identity, str) or not re.fullmatch("[0-9a-f]{64}", identity)
                or identity in identities or not isinstance(text, str)
                or not 1 <= len(text) <= 1200 or not text.strip()
                or any((ord(c) < 32 and c not in "\t\n\r") or ord(c) == 127 for c in text)):
            raise EmbeddingError("embedding_invalid_input")
        try:
            text.encode("utf-8")
        except UnicodeError:
            raise EmbeddingError("embedding_invalid_input") from None
        identities.add(identity)


def window_ranges(text, count_tokens):
    """Halve over-budget ranges; never assume token counts are monotonic."""
    ranges, pending = [], [(0, len(text))]
    while pending:
        start, end = pending.pop()
        if count_tokens(text[start:end]) <= 128:
            ranges.append((start, end))
        else:
            if end - start <= 1:
                raise EmbeddingError("embedding_window_limit")
            middle = (start + end) // 2
            pending.extend(((middle, end), (start, middle)))
        if len(ranges) + len(pending) > 64:
            raise EmbeddingError("embedding_window_limit")
    return ranges


def validate_result(result, chunks):
    _validate_encoded(result, chunks, tokenized=False)


def validate_prepared(result, chunks):
    _validate_encoded(result, chunks, tokenized=True)


def _validate_encoded(result, chunks, *, tokenized):
    def invalid():
        raise EmbeddingError("embedding_invalid_result")

    if (not isinstance(result, dict) or set(result) != {"embedding_revision", "chunks"}
            or result["embedding_revision"] != EMBEDDING_REVISION
            or not isinstance(result["chunks"], list) or len(result["chunks"]) != len(chunks)):
        invalid()
    total = 0
    for encoded, source in zip(result["chunks"], chunks):
        if (not isinstance(encoded, dict) or set(encoded) != {"chunk_id", "text_sha256", "windows"}
                or encoded["chunk_id"] != source["chunk_id"]
                or encoded["text_sha256"] != sha256(source["text"].encode("utf-8")).hexdigest()
                or not isinstance(encoded["windows"], list) or not 1 <= len(encoded["windows"]) <= 64):
            invalid()
        cursor = 0
        total += len(encoded["windows"])
        if total > 128:
            invalid()
        for window in encoded["windows"]:
            fields = {"ids", "mask"} if tokenized else {"vector"}
            if not isinstance(window, dict) or set(window) != {"char_start", "char_end"} | fields:
                invalid()
            start, end = window["char_start"], window["char_end"]
            if (type(start) is not int or type(end) is not int or start != cursor
                    or not start < end <= len(source["text"])):
                invalid()
            if tokenized:
                ids, mask = window["ids"], window["mask"]
                # Fixed config.json declares 250037 embeddings; no padding is sent.
                if (not isinstance(ids, list) or not 1 <= len(ids) <= 128
                        or any(type(i) is not int or not 0 <= i < 250037 for i in ids)
                        or not isinstance(mask, list) or len(mask) != len(ids)
                        or any(type(i) is not int or i != 1 for i in mask)):
                    invalid()
            else:
                vector = window["vector"]
                if (not isinstance(vector, list) or len(vector) != 384
                        or any(type(v) not in (int, float) or not -1.00001 <= v <= 1.00001 for v in vector)
                        or not math.isclose(math.fsum(v*v for v in vector), 1.0, abs_tol=1e-5)):
                    invalid()
            cursor = end
        if cursor != len(source["text"]):
            invalid()


def _run_child(command, payload, timeout, *, output_limit=MAX_OUTPUT_BYTES):
    env = {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
           "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TOKENIZERS_PARALLELISM": "false",
           "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
    with tempfile.TemporaryFile() as output:
        try:
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output,
                                  stderr=subprocess.DEVNULL, env=env, close_fds=True,
                                  start_new_session=True, cwd="/") as child:
                try:
                    child.communicate(input=payload, timeout=timeout)
                except subprocess.TimeoutExpired:
                    raise EmbeddingError("embedding_timeout") from None
                finally:
                    # Also remove descendants if a failed child exited before them.
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.communicate()
                if child.returncode in (-signal.SIGKILL, -signal.SIGXCPU, -signal.SIGXFSZ):
                    raise EmbeddingError("embedding_resource_limit")
                if child.returncode != 0:
                    raise EmbeddingError("embedding_worker_failed")
        except OSError:
            raise EmbeddingError("embedding_worker_failed") from None
        output.seek(0)
        raw = output.read(output_limit + 1)
    if len(raw) > output_limit:
        raise EmbeddingError("embedding_result_too_large")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise EmbeddingError("embedding_invalid_result") from None
    if not isinstance(result, dict):
        raise EmbeddingError("embedding_invalid_result")
    if "error" in result:
        code = result["error"]
        raise EmbeddingError(code if isinstance(code, str) and code in CHILD_ERRORS
                             else "embedding_worker_failed")
    return result


def encode_chunks(chunks, *, model_dir, python_executable, lock_path, timeout_seconds=60):
    """Encode one bounded batch; no publication or remote access, no partial result."""
    validate_chunks(chunks)
    if (type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 60
            or any(not isinstance(p, str) or not os.path.isabs(p) or p == "/" or "\x00" in p
                   for p in (model_dir, python_executable, lock_path))):
        raise EmbeddingError("embedding_invalid_input")
    if os.geteuid() == 0:
        raise EmbeddingError("embedding_must_be_unprivileged")
    payload = json.dumps(chunks, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_INPUT_BYTES:
        raise EmbeddingError("embedding_invalid_input")
    from .worker import WorkerError, worker_lock
    try:
        # ponytail: one shared lock on this ECS; reconsider only after measured throughput needs.
        with worker_lock(lock_path):
            deadline = monotonic() + timeout_seconds
            def remaining():
                seconds = deadline - monotonic()
                if seconds <= 0:
                    raise EmbeddingError("embedding_timeout")
                return seconds
            command = [python_executable, "-I", "-B", str(Path(__file__).resolve()), model_dir]
            # Sibling processes: tokenizer exits before ORT loads, no nested sessions.
            prepared = _run_child(command + ["tokenize"], payload, remaining(), output_limit=MAX_STAGE_BYTES)
            remaining()
            validate_prepared(prepared, chunks)
            payload = json.dumps({"chunks": chunks, "prepared": prepared}, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8")
            if len(payload) > MAX_STAGE_BYTES:
                raise EmbeddingError("embedding_result_too_large")
            result = _run_child(command + ["infer"], payload, remaining())
            validate_result(result, chunks)
            remaining()
            return result
    except WorkerError as error:
        raise EmbeddingError("embedding_busy" if str(error) == "worker_already_running"
                             else "embedding_lock_unavailable") from None


def _set_limits():
    if os.geteuid() == 0:
        raise EmbeddingError("embedding_must_be_unprivileged")
    for kind, soft_bound, hard_bound in ((resource.RLIMIT_AS, 2*1024**3, 2*1024**3),
            (resource.RLIMIT_CPU, 60, 65), (resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES),
            (resource.RLIMIT_CORE, 0, 0)):
        soft, hard = resource.getrlimit(kind)
        new_hard = hard_bound if hard == resource.RLIM_INFINITY else min(hard, hard_bound)
        new_soft = min(soft_bound, new_hard, soft if soft != resource.RLIM_INFINITY else soft_bound)
        resource.setrlimit(kind, (new_soft, new_hard))


def _verify_model(model_dir, *, tokenizer_only=False):
    try:
        for name, expected in MODEL_HASHES.items():
            if tokenizer_only and name.endswith(".onnx"):
                continue
            path = Path(model_dir) / name
            if not path.is_file() or path.stat().st_size > 256*1024*1024:
                raise EmbeddingError("embedding_model_unavailable")
            digest = sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024*1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise EmbeddingError("embedding_model_unavailable")
    except OSError:
        raise EmbeddingError("embedding_model_unavailable") from None


def _verify_dependencies():
    try:
        if any(importlib.metadata.version(name) != version for name, version in VERSIONS.items()):
            raise EmbeddingError("embedding_dependency_unavailable")
    except (ImportError, importlib.metadata.PackageNotFoundError):
        raise EmbeddingError("embedding_dependency_unavailable") from None


def _tokenize(chunks, model_dir):
    _verify_model(model_dir, tokenizer_only=True)
    _verify_dependencies()
    from fastembed.common.preprocessor_utils import load_tokenizer
    tokenizer, _ = load_tokenizer(Path(model_dir))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    ranges = [window_ranges(c["text"], lambda text: len(tokenizer.encode(text).ids)) for c in chunks]
    if sum(map(len, ranges)) > 128:
        raise EmbeddingError("embedding_window_limit")
    result = {"embedding_revision": EMBEDDING_REVISION, "chunks": []}
    for chunk, windows in zip(chunks, ranges):
        encoded = {"chunk_id": chunk["chunk_id"],
                   "text_sha256": sha256(chunk["text"].encode("utf-8")).hexdigest(), "windows": []}
        for start, end in windows:
            tokens = tokenizer.encode_batch([chunk["text"][start:end]])[0]
            encoded["windows"].append({"char_start": start, "char_end": end,
                                       "ids": tokens.ids, "mask": tokens.attention_mask})
        result["chunks"].append(encoded)
    validate_prepared(result, chunks)
    return result


def _infer(chunks, prepared, model_dir):
    validate_prepared(prepared, chunks)
    _verify_model(model_dir)
    _verify_dependencies()
    import numpy as np
    import onnxruntime as ort
    from fastembed.common.utils import mean_pooling
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    model = ort.InferenceSession(str(Path(model_dir)/"model_optimized.onnx"),
                                providers=["CPUExecutionProvider"], sess_options=options)
    if model.get_providers() != ["CPUExecutionProvider"]:
        raise EmbeddingError("embedding_dependency_unavailable")
    names = {node.name for node in model.get_inputs()}
    if names != {"input_ids", "attention_mask", "token_type_ids"}:
        raise EmbeddingError("embedding_dependency_unavailable")
    result = {"embedding_revision": EMBEDDING_REVISION, "chunks": []}
    for source in prepared["chunks"]:
        encoded = {"chunk_id": source["chunk_id"], "text_sha256": source["text_sha256"], "windows": []}
        for window in source["windows"]:
            ids = np.array([window["ids"]], dtype=np.int64)
            mask = np.array([window["mask"]], dtype=np.int64)
            output = model.run(None, {"input_ids": ids, "attention_mask": mask,
                                      "token_type_ids": np.zeros_like(ids)})[0]
            vector = [float(v) for v in mean_pooling(output, mask).astype(output.dtype, copy=False)[0]]
            if len(vector) != 384 or any(not math.isfinite(v) for v in vector):
                raise EmbeddingError("embedding_invalid_result")
            norm = math.sqrt(math.fsum(v*v for v in vector))
            if not math.isfinite(norm) or norm == 0:
                raise EmbeddingError("embedding_invalid_result")
            encoded["windows"].append({"char_start": window["char_start"], "char_end": window["char_end"],
                                       "vector": [v/norm for v in vector]})
        result["chunks"].append(encoded)
    validate_result(result, chunks)
    return result


def _main():
    try:
        _set_limits()
        if (len(sys.argv) != 3 or not os.path.isabs(sys.argv[1])
                or sys.argv[2] not in ("tokenize", "infer")):
            raise EmbeddingError("embedding_invalid_input")
        bound = MAX_INPUT_BYTES if sys.argv[2] == "tokenize" else MAX_STAGE_BYTES
        raw = sys.stdin.buffer.read(bound + 1)
        if len(raw) > bound:
            raise EmbeddingError("embedding_invalid_input")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise EmbeddingError("embedding_invalid_input") from None
        if sys.argv[2] == "tokenize":
            validate_chunks(payload)
            result = _tokenize(payload, sys.argv[1])
        else:
            if not isinstance(payload, dict) or set(payload) != {"chunks", "prepared"}:
                raise EmbeddingError("embedding_invalid_input")
            validate_chunks(payload["chunks"])
            result = _infer(payload["chunks"], payload["prepared"], sys.argv[1])
        output = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
        if len(output) > MAX_OUTPUT_BYTES:
            raise EmbeddingError("embedding_resource_limit")
    except EmbeddingError as error:
        output = json.dumps({"error": error.code}).encode("ascii")
    except MemoryError:
        output = b'{"error":"embedding_resource_limit"}'
    except ImportError:
        output = b'{"error":"embedding_dependency_unavailable"}'
    except Exception:
        output = b'{"error":"embedding_worker_failed"}'
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    _main()
