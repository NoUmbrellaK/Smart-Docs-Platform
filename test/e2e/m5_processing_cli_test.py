"""Real persistent parser/search CLIs with isolated SQL; no encoder or remote provider."""
from contextlib import contextmanager
import http.client
import json
import os
import subprocess
import sys
import time
import unittest

from m1_http_test import HttpClient, ServerProcess, data
from m2_worker_test import ROOT, WorkerE2E


class ProcessingCLIE2E(WorkerE2E):
    @contextmanager
    def process(self, name, command, environment):
        path = self.root / (name + ".log")
        with path.open("wb") as log:
            process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
            try:
                yield process
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(5)
                        self.fail(name + " required forced termination")
                self.assertNotIn(self.token, path.read_text(errors="replace"))

    def parsed(self, process, jobs):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.assertIsNone(process.poll(), "parser CLI exited")
            states = [self.state(job)["state"] for job in jobs]
            if states == ["completed"] * len(jobs):
                return
            self.assertTrue(all(state in ("pending", "running", "completed") for state in states), states)
            time.sleep(0.1)
        self.fail("persistent parser did not publish within 30 seconds")

    def test_persistent_cli_restart_and_search_boundary(self):
        # Broken CLI environment wiring, idle-loop processing, lock exclusivity,
        # session forwarding or fresh authorization must fail this test.
        base_environment = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "python")}
        parser_environment = {**base_environment, "SMARTDOCS_WORKER_TOKEN": self.token,
            "SMARTDOCS_WORKER_PORT": str(self.server.port),
            "SMARTDOCS_WORKER_LOCK_PATH": str(self.root / "persistent-worker.lock")}
        parser_command = [sys.executable, "-B", "-m", "smartdocs.worker"]
        selected = self.upload("public.txt", b"Public backup recovery instructions.", "text/plain")
        internal = self.upload("internal.txt", b"INTERNAL-MARKER backup recovery instructions.", "text/plain")
        with self.process("parser-first", parser_command, parser_environment) as parser:
            self.parsed(parser, [selected, internal])
            duplicate = subprocess.run([*parser_command, "--once"], env=parser_environment,
                                       capture_output=True, text=True, timeout=15)
            self.assertEqual(duplicate.returncode, 1)
            self.assertIn("worker_already_running", duplicate.stderr)
            self.assertNotIn(self.token, duplicate.stdout + duplicate.stderr)
            self.assertIsNone(parser.poll())
            later = self.upload("later.txt", b"Uploaded after the first batch finished.", "text/plain")
            self.parsed(parser, [later])
        print("PASS persistent parser processes uploads; duplicate instance refused", flush=True)

        queued = self.upload("after-stop.md", b"# Restart\n\nQueued after parser stopped.", "text/markdown")
        self.assertEqual(self.state(queued)["state"], "pending")
        with self.process("parser-restarted", parser_command, parser_environment) as parser:
            self.parsed(parser, [queued])
            self.assertEqual(self.document(selected)["parse_state"], "completed")
        print("PASS stopped parser releases lock; restart processes queued Markdown", flush=True)

        project_path = f'/api/v1/projects/{self.project["id"]}'
        policy_path = project_path + "/files/" + selected["file_id"] + "/remote-ai-policy"
        data(self.user.request("PUT", policy_path, {
            "policy": "remote_ai_allowed", "approved_version_ids": [selected["version_id"]]}))
        gateway_port = ServerProcess._free_port()
        origin = f"http://127.0.0.1:{self.server.port}"
        search_environment = {**base_environment, "SMARTDOCS_PUBLIC_ORIGIN": origin,
            "SMARTDOCS_ENCODER_SOCKET": str(self.root / "absent-encoder.sock")}
        search_command = [sys.executable, "-B", "-m", "smartdocs.search_http",
                          "--port", str(gateway_port), "--backend-port", str(self.server.port)]
        gateway = HttpClient(gateway_port)
        gateway.cookies = self.user.cookies
        anonymous = HttpClient(gateway_port)
        headers = {"Host": f"127.0.0.1:{self.server.port}", "Origin": origin}
        path = project_path + "/hybrid-search"
        saved_chunk = None
        for sequence in (1, 2):
            with self.process(f"search-{sequence}", search_command, search_environment) as search:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    self.assertIsNone(search.poll(), "search CLI exited")
                    try:
                        if anonymous.request("POST", "/").status == 404:
                            break
                    except (OSError, http.client.HTTPException):
                        pass
                    time.sleep(0.1)
                else:
                    self.fail("search CLI readiness timeout")
                self.assertEqual(anonymous.request("POST", path, {"query": "backup"}, headers).status, 401)
                denied = gateway.request("POST", path, {"query": "backup"},
                                         {**headers, "Origin": "https://wrong.invalid"})
                self.assertEqual(denied.status, 403)
                found = data(gateway.request("POST", path, {"query": "backup"}, headers))
                self.assertEqual(found["retrieval"], "keyword_only")
                self.assertIn("query_embedding_unavailable", found["warnings"])
                self.assertEqual([hit["file_id"] for hit in found["items"]], [selected["file_id"]])
                self.assertNotIn("INTERNAL-MARKER", json.dumps(found))
                chunk = found["items"][0]["chunk"]
                self.assertEqual(chunk["version_id"], selected["version_id"])
                if saved_chunk is not None:
                    self.assertEqual(chunk, saved_chunk)
                    data(self.user.request("PUT", policy_path, {
                        "policy": "remote_ai_allowed", "approved_version_ids": []}))
                    revoked = data(gateway.request("POST", path, {"query": "backup"}, headers))
                    self.assertEqual(revoked["items"], [])
                saved_chunk = chunk
                source = data(self.user.request("GET", self.version_path(selected) + "/chunks/" + chunk["chunk_id"]))
                self.assertEqual(source["chunk"], chunk)
                self.assertIsNone(search.poll())
        print("PASS search CLI restart preserves fixed source; rejects anonymous, wrong Origin and revoked data",
              flush=True)
        print(json.dumps({"case": "persistent-processing-cli", "checks": 3, "result": "pass",
                          "remote_model_calls": 0, "encoder": "not_started",
                          "production_deployment": "not_run"}), flush=True)


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("run as an unprivileged isolated test account")
    suite = unittest.TestSuite([ProcessingCLIE2E("test_persistent_cli_restart_and_search_boundary")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
