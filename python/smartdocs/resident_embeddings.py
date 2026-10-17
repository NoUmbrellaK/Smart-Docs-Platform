"""Verified resident encoding core; not a service or authorization endpoint.

Call serially in a capped, unprivileged, network-isolated process. The service
owner must supply startup/request watchdogs and kill/restart after a timeout.
The opt-in resident_service supplies that supervision. Tokenizers are transient.
"""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import weakref

from . import embeddings as base


@contextmanager
def verified_snapshot(model_dir, name):
    """Consume exactly the SHA-verified bytes through a Linux sealed memfd."""
    try:
        fd = os.open(Path(model_dir)/name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 256*1024*1024:
                raise base.EmbeddingError("embedding_model_unavailable")
            memfd = os.memfd_create("smartdocs-model", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            with os.fdopen(memfd, "w+b") as snapshot:
                digest, total = sha256(), 0
                while block := source.read(1024*1024):
                    total += len(block)
                    if total > 256*1024*1024:
                        raise base.EmbeddingError("embedding_model_unavailable")
                    digest.update(block)
                    snapshot.write(block)
                if digest.hexdigest() != base.MODEL_HASHES[name]:
                    raise base.EmbeddingError("embedding_model_unavailable")
                snapshot.flush()
                fcntl.fcntl(memfd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
                snapshot.seek(0)
                yield snapshot
    except OSError:
        raise base.EmbeddingError("embedding_model_unavailable") from None


class ResidentEncoder:
    def __init__(self, model_dir):
        if os.geteuid() == 0:
            raise base.EmbeddingError("embedding_must_be_unprivileged")
        if not isinstance(model_dir, str) or not os.path.isabs(model_dir) or "\x00" in model_dir:
            raise base.EmbeddingError("embedding_invalid_input")
        base._verify_dependencies()
        # Validate every artifact before any native constructor sees model bytes.
        with ExitStack() as stack:
            snapshots = {name: stack.enter_context(verified_snapshot(model_dir, name))
                         for name in sorted(base.MODEL_HASHES, key=lambda name: name.endswith(".onnx"))}
            weights = snapshots["model_optimized.onnx"]
            self._model = base._new_session(f"/proc/self/fd/{weights.fileno()}")
            # No provider reset/recreation may reopen the now-closed snapshot path.
            self._model.disable_fallback()
            weights.close()
            # Retain only sealed bytes, not the native tokenizer's large live heap.
            with ExitStack() as retained:
                fds = []
                for name in ("tokenizer.json", "special_tokens_map.json"):
                    fd = os.dup(snapshots[name].fileno())
                    retained.callback(os.close, fd)
                    fds.append(fd)
                self._tokenizer_fds = tuple(fds)
                self._finalizer = weakref.finalize(self, retained.pop_all().close)

    def close(self):
        self._finalizer()
        self._model = None

    def encode(self, chunks):
        if not self._finalizer.alive:
            raise base.EmbeddingError("embedding_worker_failed")
        base.validate_chunks(chunks)
        if len(json.dumps(chunks, ensure_ascii=False, separators=(",", ":")).encode()) > base.MAX_INPUT_BYTES:
            raise base.EmbeddingError("embedding_invalid_input")
        prepared = _tokenize_snapshot(chunks, self._tokenizer_fds)
        result = base._infer_windows(chunks, prepared, self._model)
        if len(json.dumps(result, allow_nan=False, separators=(",", ":")).encode()) > base.MAX_OUTPUT_BYTES:
            raise base.EmbeddingError("embedding_result_too_large")
        return result


def _tokenizer_command(fds):
    return [sys.executable, "-B", "-m", "smartdocs.resident_embeddings", "--tokenize", *map(str, fds)]


def _tokenize_snapshot(chunks, fds, *, timeout=60):
    base.validate_chunks(chunks)
    payload = json.dumps(chunks, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > base.MAX_INPUT_BYTES or not 0 < timeout <= 60:
        raise base.EmbeddingError("embedding_invalid_input")
    environment = {"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
    try:
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(_tokenizer_command(fds), stdin=subprocess.PIPE,
                stdout=output, stderr=subprocess.DEVNULL, env=environment, pass_fds=fds,
                start_new_session=False)  # Stay in the model's supervised process group.
            try:
                child.communicate(payload, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise base.EmbeddingError("embedding_timeout") from None
            finally:
                if child.poll() is None: child.kill()
                try: child.wait(timeout=5)
                finally:
                    if child.stdin: child.stdin.close()
            if child.returncode != 0:
                raise base.EmbeddingError("embedding_worker_failed")
            output.seek(0)
            raw = output.read(base.MAX_STAGE_BYTES+1)
    except (OSError, subprocess.TimeoutExpired):
        raise base.EmbeddingError("embedding_worker_failed") from None
    if len(raw) > base.MAX_STAGE_BYTES:
        raise base.EmbeddingError("embedding_result_too_large")
    try: result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise base.EmbeddingError("embedding_invalid_result") from None
    if isinstance(result, dict) and "error" in result:
        code = result["error"]
        raise base.EmbeddingError(code if isinstance(code, str) and code in base.CHILD_ERRORS
                                  else "embedding_worker_failed")
    base.validate_prepared(result, chunks)
    return result


def _tokenizer_main():
    try:
        base._set_limits()
        if len(sys.argv) != 4 or sys.argv[1] != "--tokenize":
            raise base.EmbeddingError("embedding_invalid_input")
        raw = sys.stdin.buffer.read(base.MAX_INPUT_BYTES+1)
        if len(raw) > base.MAX_INPUT_BYTES:
            raise base.EmbeddingError("embedding_invalid_input")
        try: chunks = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise base.EmbeddingError("embedding_invalid_input") from None
        base.validate_chunks(chunks)
        try:
            fds = tuple(map(int, sys.argv[2:]))
            seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
            for fd, name in zip(fds, ("tokenizer.json", "special_tokens_map.json")):
                if (fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals != seals
                        or not 0 < os.fstat(fd).st_size <= 256*1024**2):
                    raise base.EmbeddingError("embedding_model_unavailable")
                digest = sha256()
                with open(f"/proc/self/fd/{fd}", "rb") as source:
                    for block in iter(lambda: source.read(1024*1024), b""):
                        digest.update(block)
                if digest.hexdigest() != base.MODEL_HASHES[name]:
                    raise base.EmbeddingError("embedding_model_unavailable")
        except (ValueError, OSError):
            raise base.EmbeddingError("embedding_model_unavailable") from None
        base._verify_dependencies()
        from tokenizers import AddedToken, Tokenizer
        tokenizer = Tokenizer.from_file(f"/proc/self/fd/{fds[0]}")
        with open(f"/proc/self/fd/{fds[1]}") as source:
            for token in json.load(source).values():
                tokenizer.add_special_tokens([AddedToken(**token)])
        tokenizer.no_truncation()
        tokenizer.no_padding()
        result = base._prepare_chunks(chunks, tokenizer)
        output = json.dumps(result, allow_nan=False, separators=(",", ":")).encode()
        if len(output) > base.MAX_STAGE_BYTES:
            raise base.EmbeddingError("embedding_result_too_large")
    except base.EmbeddingError as error:
        output = json.dumps({"error": error.code}).encode()
    except Exception:
        output = b'{"error":"embedding_worker_failed"}'
    sys.stdout.buffer.write(output)


if __name__ == "__main__": _tokenizer_main()
