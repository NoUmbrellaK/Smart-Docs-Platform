"""Verified resident encoding core; not a service or authorization endpoint.

Call serially in a capped, unprivileged, network-isolated process. The service
owner must supply startup/request watchdogs and kill/restart after a timeout.
This core is not yet wired to the production socket service.
"""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import stat

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
            from tokenizers import AddedToken, Tokenizer
            tokenizer = snapshots["tokenizer.json"]
            self._tokenizer = Tokenizer.from_file(f"/proc/self/fd/{tokenizer.fileno()}")
            tokens = json.load(snapshots["special_tokens_map.json"])
            for token in tokens.values():
                self._tokenizer.add_special_tokens([AddedToken(**token)])
            self._tokenizer.no_truncation()
            self._tokenizer.no_padding()

    def encode(self, chunks):
        base.validate_chunks(chunks)
        if len(json.dumps(chunks, ensure_ascii=False, separators=(",", ":")).encode()) > base.MAX_INPUT_BYTES:
            raise base.EmbeddingError("embedding_invalid_input")
        prepared = base._prepare_chunks(chunks, self._tokenizer)
        result = base._infer_windows(chunks, prepared, self._model)
        if len(json.dumps(result, allow_nan=False, separators=(",", ":")).encode()) > base.MAX_OUTPUT_BYTES:
            raise base.EmbeddingError("embedding_result_too_large")
        return result
