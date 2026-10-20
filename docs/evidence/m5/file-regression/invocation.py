"""Run existing HTTP and interruption assertions; not the full M1 acceptance runner.

Invoke from a verified execution copy under scripts/test-with-mysql.sh.
Only artificial temporary data is used; structured evidence goes to stdout.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


root = Path.cwd()
if not re.fullmatch(r"/tmp/smartdocs-mysql\.[^/]+/mysql.sock",
                    os.environ.get("SMARTDOCS_MYSQL_SOCKET", "")):
    raise RuntimeError("isolated MySQL wrapper required")
sys.path.insert(0, str(root / "test/e2e"))
from m1_interrupt_test import FAULT_ROUNDS, HttpClient, ServerProcess, check, login, run_round

with tempfile.TemporaryDirectory(prefix="smartdocs-m5-file.") as directory:
    scratch = Path(directory)
    for name in ("storage", "storage/objects", "storage/staging"):
        (scratch / name).mkdir(mode=0o700)
    os.environ.update(
        SMARTDOCS_STORAGE_ROOT=str(scratch / "storage"),
        SMARTDOCS_MAX_FILE_BYTES="1048576", SMARTDOCS_CHUNK_BYTES="16",
        SMARTDOCS_MAX_JSON_BYTES="65536", SMARTDOCS_MYSQL_POOL_SIZE="4",
        SMARTDOCS_THREAD_COUNT="4", SMARTDOCS_PASSWORD_ITERATIONS="1000",
        SMARTDOCS_LOG_LEVEL="0", SMARTDOCS_SECURE_COOKIE="false",
        SMARTDOCS_E2E_PASSWORD="m5-file-test-only")
    context_path, http_path = scratch / "context.json", scratch / "http.json"
    try:
        subprocess.run([
            sys.executable, "-B", str(root / "test/e2e/m1_http_test.py"),
            "--server-bin", str(root / "bin/server"),
            "--admin-bin", str(root / "bin/smartdocs-admin"),
            "--log-dir", str(scratch / "http-logs"),
            "--context-file", str(context_path), "--evidence-file", str(http_path),
            "--plain", str(root / "test/fixtures/m1/plain.txt"),
            "--pdf", str(root / "test/fixtures/m1/sample.pdf")], check=True)
    finally:
        if http_path.exists():
            print("FILE_HTTP_JSON=" + http_path.read_text().replace("\n", ""), flush=True)
    context = json.loads(context_path.read_text())
    server = ServerProcess(root / "bin/smartdocs_test_server", scratch / "fault-logs")
    client, rounds = HttpClient(0), []
    try:
        server.start()
        client.port = server.port
        login(client, context["username"], os.environ["SMARTDOCS_E2E_PASSWORD"])
        server.stop()
        for point, count in FAULT_ROUNDS:
            for _ in range(count):
                result = run_round(server, client, context["project_id"],
                                   context["root_directory_id"], point, len(rounds) + 1)
                rounds.append(result)
                print("FILE_ROUND_JSON=" + json.dumps(result, sort_keys=True), flush=True)
    finally:
        server.stop()
    distribution = {point: sum(r["fault_point"] == point for r in rounds)
                    for point, _ in FAULT_ROUNDS}
    check(distribution == dict(FAULT_ROUNDS), "fault distribution mismatch")
    check(len(rounds) == 20 and all(r["status"] == "pass" for r in rounds),
          "not all twenty rounds passed")
    print("FILE_SUMMARY_JSON=" + json.dumps({
        "rounds": len(rounds), "passed": sum(r["status"] == "pass" for r in rounds),
        "assertions": sum(r["assertions"] for r in rounds),
        "fault_distribution": distribution,
        "scope": "existing HTTP and interruption assertions only"}, sort_keys=True), flush=True)
