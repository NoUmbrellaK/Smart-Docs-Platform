"""Real private resident service/client, run non-root in original offline cgroup."""
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"python"))
from smartdocs.vector_worker import request_encoding


def main():
    assert os.geteuid() != 0
    for family in (socket.AF_INET, socket.AF_INET6):
        try: probe = socket.socket(family)
        except OSError: continue
        probe.close(); raise AssertionError("requires blocked IP sockets")
    texts = ['Database disaster recovery: restore the latest backup to recover lost data.',
             '部署流程与备份恢复验证。'*80, 'ＡＢＣ café e\u0301 👩‍💻\n备份\t恢复',
             '<s> [MASK] </s>', '如何恢复备份？', 'x'*1200]
    chunks = [{'chunk_id': str(i)*64, 'text': text} for i, text in enumerate(texts)]
    with tempfile.TemporaryDirectory(prefix="resident-service-") as directory:
        path = directory+"/encoder.sock"
        start = time.monotonic()
        with subprocess.Popen([sys.executable, "-B", "-m", "smartdocs.resident_service", path,
                               sys.argv[1], sys.argv[2], directory+"/model.lock"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as parent:
            try:
                while not os.path.exists(path):
                    assert parent.poll() is None, parent.stderr.read().decode()
                    assert time.monotonic()-start < 185, "startup watchdog did not terminate"
                    time.sleep(0.1)
                startup = time.monotonic()-start
                children_path = Path(f"/proc/{parent.pid}/task/{parent.pid}/children")
                children = children_path.read_text().split()
                assert len(children) == 1, children
                child = int(children[0])
                durations = []
                for _ in range(3):
                    start = time.monotonic()
                    result = request_encoding(path, chunks)
                    durations.append(round(time.monotonic()-start, 3))
                    digest = sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
                    assert digest == "6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8", digest
                    assert children_path.read_text().split() == children
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.settimeout(3); connection.connect(path)
                    connection.sendall(b"{}"); connection.shutdown(socket.SHUT_WR)
                    assert json.loads(connection.recv(1024)) == {"error": "embedding_invalid_input"}
                assert request_encoding(path, chunks) == result
                assert children_path.read_text().split() == children
                status = dict(line.split(":", 1) for line in Path(f"/proc/{child}/status").read_text().splitlines())
            finally:
                if parent.poll() is None: parent.terminate()
                try: parent.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    parent.kill(); parent.wait()
                    raise AssertionError("supervisor did not reap on SIGTERM")
            assert parent.returncode == 0, parent.stderr.read().decode()
        assert not Path(f"/proc/{child}").exists(), "model child survived supervisor"
        assert not os.path.exists(path)
    print(json.dumps({"passed": True, "cases": ["ready_after_load", "unchanged_private_client",
        "same_child_three_requests", "old_fingerprint", "invalid_input_then_reuse", "sigterm_reaps_model"],
        "startup_seconds": round(startup, 3), "request_seconds": durations,
        "model_rss": status["VmRSS"].strip(), "model_peak_rss": status["VmHWM"].strip(),
        "compatibility_sha256": digest, "uid": os.geteuid(), "ip_sockets_blocked": True}, indent=2))


if __name__ == "__main__": main()
