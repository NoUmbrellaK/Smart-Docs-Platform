"""Real TLS/search/Agent/stdio MCP/C++/SQL; only provider transport is mocked."""
import argparse
from contextlib import contextmanager, ExitStack
from functools import partial
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from m1_http_test import HttpClient, data, login, ServerProcess
from m3_agent_http_test import AgentHttpE2E, tool
from m5_tls_proxy_test import TLSClient, configuration
from smartdocs import search_http, vector_worker, worker


@contextmanager
def tls_gateways(fixture, respond):
    original_user = fixture.user
    fixture.server.stop()
    fixture.server.start(environment_overrides={
        "SMARTDOCS_WORKER_TOKEN": fixture.token, "SMARTDOCS_SECURE_COOKIE": "true"})
    fixture.client = worker.Client(fixture.server.port, fixture.token)
    try:
        with tempfile.TemporaryDirectory(prefix="tls-", dir=fixture.root) as temporary, ExitStack() as stack:
            directory = Path(temporary)
            port = ServerProcess._free_port()
            origin = f"https://127.0.0.1:{port}"
            stack.enter_context(fixture.gateway(respond, origin=origin))
            # Exercise the real unavailable-socket path, never fabricate a successful vector.
            search = search_http.SearchHTTPServer(0, fixture.server.port, origin,
                partial(vector_worker.request_encoding, str(directory / "absent-encoder.sock")))
            thread = threading.Thread(target=search.serve_forever)
            thread.start()

            def close_search():
                search.shutdown()
                search.server_close()
                thread.join(5)
                fixture.assertFalse(thread.is_alive())

            stack.callback(close_search)
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-days", "1", "-subj", "/CN=127.0.0.1",
                            "-addext", "subjectAltName=IP:127.0.0.1",
                            "-keyout", str(directory / "key.pem"), "-out", str(directory / "cert.pem")],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
            config = directory / "nginx.conf"
            config.write_text(configuration(directory, port, fixture.server.port,
                                             search.server_port, fixture.gateway_port))
            command = [str(fixture.nginx), "-p", str(directory), "-c", str(config)]
            subprocess.run([*command, "-t"], check=True, capture_output=True, timeout=15)
            log = stack.enter_context((directory / "nginx.log").open("wb"))
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)

            def stop():
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(5)

            stack.callback(stop)
            client = TLSClient(port, directory / "cert.pem", timeout=240)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("isolated nginx exited")
                try:
                    if client.request("GET", "/api/v1/health/ready").status == 200:
                        break
                except (OSError, http.client.HTTPException):
                    time.sleep(0.1)
            else:
                raise RuntimeError("isolated TLS readiness timeout")
            fixture.user = client
            login(client, "worker-e2e-admin", fixture.password)
            yield TLSClient(port, directory / "cert.pem")
    finally:
        # Restore the inherited fixture's direct HTTP transport before its normal teardown.
        fixture.server.stop()
        fixture.server.start(environment_overrides={
            "SMARTDOCS_WORKER_TOKEN": fixture.token, "SMARTDOCS_SECURE_COOKIE": "false"})
        original_user.port = fixture.server.port
        fixture.user = original_user
        fixture.client = worker.Client(fixture.server.port, fixture.token)


class TLSGatewaysE2E(AgentHttpE2E):
    def test_tls_real_gateways_and_revocation(self):
        async def respond(body):
            messages = body["messages"]
            if len(self.calls) == 1:
                return tool("search_documents", {"mode": "keyword", "query": "zzzzzz"})
            result = json.loads(messages[-1]["content"])["data"]
            if len(self.calls) == 2:
                self.assertEqual(result["items"], [])
                return tool("search_documents", {"mode": "keyword", "query": "backup"}, 2)
            if len(self.calls) == 3:
                self.assertEqual([hit["file_id"] for hit in result["items"]], [job["file_id"]])
                chunk = result["items"][0]["chunk"]
                return tool("read_document_version",
                            {k: chunk[k] for k in ("file_id", "version_id", "chunk_id")}, 3)
            self.assertEqual(len(self.calls), 4)
            chunk = result["chunk"]
            self.assertIn("restore the backup", chunk["text"])
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({
                    "answer": "Restore the backup.",
                    "citations": [{k: chunk[k] for k in ("file_id", "version_id", "chunk_id")}]})}}]}

        with tls_gateways(self, respond) as anonymous:
            job = self.upload("tls-source.txt", b"SOURCE-MARKER restore the backup.", "text/plain")
            self.policy(job, True)
            self.assertEqual(worker.run_once(self.client), "completed")
            search_path = self.base() + "/hybrid-search"
            found = data(self.user.request("POST", search_path, {"query": "backup"}))
            self.assertEqual(found["retrieval"], "keyword_only")
            self.assertIn("query_embedding_unavailable", found["warnings"])
            self.assertEqual([hit["version_id"] for hit in found["items"]], [job["version_id"]])
            self.assertNotIn("vector", found["items"][0]["match_sources"])
            self.assertEqual(anonymous.request("POST", search_path, {"query": "backup"}).status, 401)
            self.assertEqual(self.user.request("POST", search_path, {"query": "backup"},
                                               {"Origin": "https://wrong.invalid"}).status, 403)
            print("PASS TLS real worker and search gateway with explicit keyword degradation", flush=True)
            task = self.create_task(job)
            path = self.tasks_path() + "/" + task["id"]
            outcome = self.user.request("POST", path + "/execute", {})
            self.assertEqual((outcome.status, outcome.json()), (200, {"data": {"state": "completed"}}),
                             {"provider_calls": len(self.calls), "fixture_errors": self.model_errors})
            self.assertEqual(len(self.calls), 4)
            saved = data(self.user.request("GET", path + "/run/result"))
            self.assertEqual(saved["model_calls"], 4)
            citation = saved["result"]["citations"][0]
            self.assertEqual((citation["file_id"], citation["version_id"]), (job["file_id"], job["version_id"]))
            source = data(self.user.request("GET", self.version_path(job) + "/chunks/" + citation["chunk_id"]))
            self.assertIn("restore the backup", json.dumps(source))
            trace = data(self.user.request("GET", path + "/tool-calls"))
            self.assertEqual([item["tool"] for item in trace["items"]],
                             ["search_documents", "search_documents", "read_document_version"])
            print("PASS TLS Agent to stdio MCP with four local provider responses and fixed source", flush=True)
            self.assertEqual(self.user.request("POST", path + "/execute", {}).status, 409)
            self.assertEqual(len(self.calls), 4)
            pending = self.create_task(job)
            self.policy(job, False)
            denied = self.user.request("POST", self.tasks_path() + "/" + pending["id"] + "/execute", {})
            self.assertEqual(denied.status, 200)
            self.assertEqual(denied.json()["data"]["state"], "failed")
            self.assertEqual(len(self.calls), 4)
            self.assertEqual(data(self.user.request("GET", self.tasks_path() + "/" + pending["id"]))["state"],
                             "failed")
            self.assertEqual(self.user.request("GET", path + "/run/result").status, 409)
            self.assertEqual(self.model_errors, [])
            print("PASS TLS duplicate rejection and revoked scope without another provider response", flush=True)
        print(json.dumps({"case": "tls-real-gateways", "result": "pass", "checks": 3,
                          "mock_provider_responses": 4, "remote_model_calls": 0,
                          "retrieval": "keyword_only", "local_embeddings": "not_run",
                          "production_deployment": "not_run"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("run as an unprivileged isolated test account")
    TLSGatewaysE2E.nginx = args.nginx.resolve()
    suite = unittest.TestSuite([TLSGatewaysE2E("test_tls_real_gateways_and_revocation")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
