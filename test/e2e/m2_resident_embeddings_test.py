"""Explicit capped/offline test of the resident core, not the production service."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import resource
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
from smartdocs import embeddings as base


def main():
    assert os.geteuid() != 0
    for family in (socket.AF_INET, socket.AF_INET6):
        try: probe = socket.socket(family)
        except OSError: continue
        probe.close()
        raise AssertionError("test requires blocked IP sockets")
    assert importlib.util.find_spec("smartdocs.resident_embeddings"), "resident core missing"
    from smartdocs.resident_embeddings import ResidentEncoder
    # Startup writes a bounded model memfd, so the request-output file limit
    # cannot apply until snapshots are closed. Never relax an inherited limit.
    for kind, bound in ((resource.RLIMIT_AS, 2*1024**3), (resource.RLIMIT_CPU, 60),
                        (resource.RLIMIT_FSIZE, 256*1024**2), (resource.RLIMIT_CORE, 0)):
        soft, hard = resource.getrlimit(kind)
        limit = min(value for value in (soft, hard, bound) if value != resource.RLIM_INFINITY)
        resource.setrlimit(kind, (limit, limit))
    model_dir = Path(sys.argv[1])
    texts = ['Database disaster recovery: restore the latest backup to recover lost data.',
             '部署流程与备份恢复验证。'*80, 'ＡＢＣ café e\u0301 👩‍💻\n备份\t恢复',
             '<s> [MASK] </s>', '如何恢复备份？', 'x'*1200]
    chunks = [{'chunk_id': str(i)*64, 'text': text} for i, text in enumerate(texts)]
    with tempfile.TemporaryDirectory(prefix="resident-model-test-") as tmp:
        # Hard links avoid copying 235 MB. Never write through these links.
        local = Path(tmp)
        for name in base.MODEL_HASHES:
            os.link(model_dir/name, local/name)
        start = time.monotonic()
        engine = ResidentEncoder(str(local))
        startup = time.monotonic()-start
        for fd in Path("/proc/self/fd").iterdir():
            try: target = os.readlink(fd)
            except FileNotFoundError: continue  # The directory iterator's own fd.
            assert "memfd:smartdocs-model" not in target, target
            assert not target.startswith(str(local)+"/"), target
        base._set_limits()  # Request-phase limits; not a service watchdog.
        # Remove only the fixture links. Original model files remain untouched.
        for name in base.MODEL_HASHES:
            (local/name).unlink()
        timings = []
        for _ in range(5):
            start = time.monotonic()
            result = engine.encode(chunks)
            timings.append(round(time.monotonic()-start, 3))
            base.validate_result(result, chunks)
            digest = sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
            assert digest == "6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8", digest
        try: engine.encode([])
        except base.EmbeddingError as error: assert error.code == "embedding_invalid_input"
        else: raise AssertionError("invalid request accepted")
        try: ResidentEncoder(str(local))
        except base.EmbeddingError as error: assert error.code == "embedding_model_unavailable"
        else: raise AssertionError("new instance accepted missing artifacts")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    print(json.dumps({"passed": True, "uid": os.geteuid(), "ip_sockets_blocked": True,
        "cases": ["sealed_content_load", "snapshot_descriptors_closed", "old_revision_compatible", "five_request_reuse",
                  "no_source_reopen", "invalid_input_refused", "new_instance_requires_artifacts"],
        "startup_seconds": round(startup, 3), "request_seconds": timings,
        "compatibility_sha256": digest, "rss_after_requests": status["VmRSS"].strip(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "engine_sha256": sha256((ROOT/"python/smartdocs/resident_embeddings.py").read_bytes()).hexdigest(),
        "shared_sha256": sha256((ROOT/"python/smartdocs/embeddings.py").read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
