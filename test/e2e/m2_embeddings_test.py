"""Explicit real-model test. Run only in the documented capped/offline unit.

Usage: python -B test/e2e/m2_embeddings_test.py MODEL_DIR ENCODER_PYTHON
"""
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
from smartdocs import embeddings as m
from smartdocs.worker import worker_lock


def main():
    assert os.geteuid() != 0
    interfaces = sorted(name for _, name in socket.if_nameindex())
    assert interfaces == ["lo"]
    model_dir, executable = sys.argv[1:]
    text = ("项目部署需要配置数据库，访问权限必须经过审核。" * 60)[:1200]
    assert len(text) == 1200
    changed = text[:1050] + ("这是完全不同的旅游行程和海边美食。" * 20)[:150]
    unicode_text = "中文🙂e\u0301\n" * 80
    chunks = [{"chunk_id": "a"*64, "text": text},
              {"chunk_id": "b"*64, "text": changed},
              {"chunk_id": "c"*64, "text": unicode_text}]
    with tempfile.TemporaryDirectory(prefix="embedding-test-") as tmp:
        lock_path = str(Path(tmp) / "encoder.lock")
        def encode(values=chunks, **overrides):
            config = dict(model_dir=model_dir, python_executable=executable, lock_path=lock_path)
            config.update(overrides)
            return m.encode_chunks(values, **config)

        def expect(code, call):
            try:
                call()
            except m.EmbeddingError as error:
                assert error.code == code, (code, error.code)
            else:
                raise AssertionError("expected " + code)

        # Flock must fail before starting a second expensive child.
        with worker_lock(lock_path):
            expect("embedding_busy", encode)
        # Timeout must release the lock and reap the worker; next call succeeds.
        expect("embedding_timeout", lambda: encode(timeout_seconds=0.001))
        start = time.monotonic()
        first = encode()
        first_seconds = time.monotonic() - start
        start = time.monotonic()
        second = encode()
        second_seconds = time.monotonic() - start
        m.validate_result(first, chunks)
        assert first == second, "fresh-process encoding is not repeatable"
        a, b, c = first["chunks"]
        assert len(a["windows"]) > 1 and len(c["windows"]) > 1
        assert a["windows"][0] == b["windows"][0], "unchanged prefix drifted"
        assert a["windows"][-1]["vector"] != b["windows"][-1]["vector"], "suffix ignored"

        # Mismatched local artifacts fail closed, before inference, without mutating the model.
        bad = Path(tmp) / "bad-model"
        bad.mkdir()
        for name in m.MODEL_HASHES:
            if name == "config.json":
                (bad / name).write_text("{}", encoding="utf-8")
            else:
                (bad / name).symlink_to(Path(model_dir) / name)
        expect("embedding_model_unavailable", lambda: encode(model_dir=str(bad)))

        # Observe actual limits in a separate real child, including inherited tighter caps.
        script = ("import sys,resource,json; sys.path.insert(0," + repr(str(ROOT / "python")) + ");"
                  "from smartdocs.embeddings import _set_limits;"
                  "resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024));"
                  "resource.setrlimit(resource.RLIMIT_CPU,(3,4));_set_limits();"
                  "print(json.dumps({str(k):resource.getrlimit(k) for k in "
                  "[resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE,resource.RLIMIT_CORE]}))")
        limits = m._run_child([executable, "-I", "-B", "-c", script], b"{}", 2)
        assert limits[str(resource.RLIMIT_AS)] == [512*1024*1024]*2
        assert limits[str(resource.RLIMIT_CPU)] == [3, 4]
        assert limits[str(resource.RLIMIT_FSIZE)] == [m.MAX_OUTPUT_BYTES]*2
        assert limits[str(resource.RLIMIT_CORE)] == [0, 0]

    # Independent real tokenizer checks AFTER children exit, avoiding two loaded models.
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json"))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    token_counts = []
    for source, encoded in zip(chunks, first["chunks"]):
        slices = [source["text"][w["char_start"]:w["char_end"]] for w in encoded["windows"]]
        assert "".join(slices) == source["text"]
        counts = [len(tokenizer.encode(piece).ids) for piece in slices]
        assert max(counts) <= 128
        token_counts.append(counts)
        assert encoded["text_sha256"] == hashlib.sha256(source["text"].encode()).hexdigest()
        assert all(abs(math.fsum(v*v for v in w["vector"]) - 1) < 1e-5 for w in encoded["windows"])
    print(json.dumps({"passed": True, "cases": ["exact_unicode_coverage", "real_token_budget",
        "suffix_affects_vector", "fresh_process_repeatability", "unit_vectors", "flock_busy",
        "timeout_then_reuse", "model_tamper_refusal", "inherited_limits_not_relaxed"],
        "embedding_revision": m.EMBEDDING_REVISION, "window_token_counts": token_counts,
        "first_seconds": round(first_seconds, 3), "second_seconds": round(second_seconds, 3),
        "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "encoder_sha256": hashlib.sha256((ROOT / "python/smartdocs/embeddings.py").read_bytes()).hexdigest(),
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "network_interfaces": interfaces, "uid": os.geteuid()}, indent=2))


if __name__ == "__main__":
    main()
