"""Real C++/MySQL + real offline encoder socket; requires the capped test environment."""
from functools import partial
from contextlib import contextmanager
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import unittest

import m2_worker_test as support
from m1_http_test import data
from smartdocs import worker, vector_worker


class VectorE2E(support.WorkerE2E):
    def setUp(self):
        super().setUp()
        self.vector = vector_worker.VectorClient(self.server.port, self.token)
        self.encoder = partial(vector_worker.request_encoding, os.environ["SMARTDOCS_ENCODER_SOCKET"])

    def policy(self, job, allowed):
        path = f'/api/v1/projects/{self.project["id"]}/files/{job["file_id"]}/remote-ai-policy'
        return data(self.user.request("PUT", path, {"policy": "remote_ai_allowed" if allowed else "internal_only",
                                                    "approved_version_ids": [job["version_id"]] if allowed else []}))

    def vector_job(self, job):
        return {**job, "processing_job_id": self.document(job)["vector_job_id"]}

    def stored_counts(self, vector):
        identity = vector["processing_job_id"]
        self.assertRegex(identity, r"^[a-f0-9]{32}$")
        sql = ("SELECT (SELECT COUNT(*) FROM embedding_batches WHERE job_id='"+identity+"'),"
               "COUNT(DISTINCT chunk_id),COUNT(*) FROM document_embeddings WHERE job_id='"+identity+"'")
        env = dict(os.environ, MYSQL_PWD=os.environ["SMARTDOCS_MYSQL_PASSWORD"])
        result = subprocess.run(["mysql", "--protocol=SOCKET", "--socket="+env["SMARTDOCS_MYSQL_SOCKET"],
                                 "--user="+env["SMARTDOCS_MYSQL_USER"], "--database="+env["SMARTDOCS_MYSQL_DATABASE"],
                                 "--batch", "--skip-column-names", "-e", sql],
                                env=env, capture_output=True, text=True, timeout=10, check=True)
        return tuple(map(int, result.stdout.split()))

    def vector_cli(self, expected=0):
        env = {"PYTHONPATH": str(support.ROOT/"python"), "SMARTDOCS_WORKER_TOKEN": self.token,
               "SMARTDOCS_WORKER_PORT": str(self.server.port),
               "SMARTDOCS_VECTOR_LOCK_PATH": str(self.root/"vector.lock"),
               "SMARTDOCS_ENCODER_SOCKET": os.environ["SMARTDOCS_ENCODER_SOCKET"]}
        result = subprocess.run([sys.executable, "-B", "-m", "smartdocs.vector_worker", "--once"],
                                env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, expected, result.stdout+result.stderr)
        self.assertNotIn(self.token, result.stdout+result.stderr)
        return result

    @contextmanager
    def gateway(self, encoder_socket):
        port = support.ServerProcess._free_port()
        host = f"127.0.0.1:{self.server.port}"
        environment = {"PYTHONPATH": str(support.ROOT/"python"),
                       "SMARTDOCS_PUBLIC_ORIGIN": "http://"+host,
                       "SMARTDOCS_ENCODER_SOCKET": encoder_socket}
        with subprocess.Popen([sys.executable, "-B", "-m", "smartdocs.search_http",
                "--port", str(port), "--backend-port", str(self.server.port)],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                deadline = time.monotonic()+10
                while True:
                    self.assertIsNone(process.poll(), "gateway exited during startup")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=1): break
                    except ConnectionRefusedError:
                        if time.monotonic() >= deadline: raise
                        time.sleep(.05)
                def query(arguments, cookie=None, expected=200):
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
                    try:
                        connection.request("POST", f'/api/v1/projects/{self.project["id"]}/hybrid-search',
                            json.dumps(arguments).encode(), {"Host": host, "Origin": "http://"+host,
                            "Content-Type": "application/json", "Cookie": cookie if cookie is not None else self.user._cookie_header()})
                        response = connection.getresponse()
                        payload = json.loads(response.read())
                        self.assertEqual(response.status, expected, payload)
                        self.assertEqual(response.getheader("Cache-Control"), "no-store")
                        return payload.get("data", payload.get("error"))
                    finally:
                        connection.close()
                yield query
            finally:
                process.terminate()
                try: output = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); output = process.communicate(timeout=5)
                self.assertNotIn(self.user._cookie_header().encode(), b"".join(output))

    def test_vector_hybrid_real_query_and_encoding_failure(self):
        recovery = self.upload("recovery.txt", b"Database disaster recovery: restore the latest backup to recover lost data.", "text/plain")
        other = self.upload("cooking.txt", b"Recipe: mix flour and eggs, bake a cake in the oven.", "text/plain")
        for job in (recovery, other):
            self.assertEqual(worker.run_once(self.client), "completed")
        for job in (recovery, other):
            self.policy(job, True); self.jobs.append(self.vector_job(job))
            self.assertEqual(vector_worker.run_once(self.vector, self.encoder), "completed")
        path = f'/api/v1/projects/{self.project["id"]}/search'
        backend = lambda request: data(self.user.request("POST", path, request))
        with self.gateway(os.environ["SMARTDOCS_ENCODER_SOCKET"]) as query:
            self.assertEqual(query({"query": "backup"}, cookie="smartdocs_session="+"0"*64,
                                   expected=401)["code"], "authentication_required")
            result = query({"query": "如何恢复备份？"})
        self.assertEqual(result["retrieval"], "hybrid")
        self.assertEqual(result["items"][0]["file_id"], recovery["file_id"])
        self.assertEqual(result["items"][0]["match_sources"], ["vector"])
        self.assertEqual(result["items"][0]["chunk"]["version_id"], recovery["version_id"])
        with self.gateway(str(self.root/"missing-query.sock")) as query:
            degraded = query({"query": "backup"})
            self.assertEqual(degraded["retrieval"], "keyword_only")
            self.assertIn("query_embedding_unavailable", degraded["warnings"])
            self.assertIn(recovery["file_id"], [item["file_id"] for item in degraded["items"]])
            self.policy(recovery, False)
            self.assertEqual(query({"query": "backup"})["items"], [])

    def test_vector_real_model_two_batches_gate_and_cli(self):
        job = self.upload("vectors.txt", ("部署文档🙂。"*1601).encode(), "text/plain")
        self.assertEqual(worker.run_once(self.client), "completed")
        self.assertEqual(self.document(job)["chunk_count"], 9)
        self.assertEqual(vector_worker.run_once(self.vector, self.encoder), "idle")
        self.policy(job, True)
        vector = self.vector_job(job); self.jobs.append(vector)
        self.assertIn("vector: completed", self.vector_cli().stdout)
        status = self.document(job)
        self.assertEqual((status["parse_state"], status["keyword_state"], status["vector_state"]),
                         ("completed", "completed", "completed"))
        self.assertEqual(self.state(vector)["state"], "completed")
        batches, chunks, windows = self.stored_counts(vector)
        self.assertEqual((batches, chunks), (2, 9))
        self.assertGreaterEqual(windows, 9)
        self.assertEqual(vector_worker.run_once(self.vector, self.encoder), "idle")

    def test_vector_revoke_after_encoding_blocks_publication_then_reapprove_works(self):
        job = self.upload("revoke.txt", "人工构造的部署材料".encode(), "text/plain")
        self.assertEqual(worker.run_once(self.client), "completed")
        self.policy(job, True)
        vector = self.vector_job(job); self.jobs.append(vector)
        def revoked(chunks):
            result = self.encoder(chunks)
            self.policy(job, False)
            return result
        self.assertEqual(vector_worker.run_once(self.vector, revoked), "discarded")
        self.assertEqual(self.document(job)["vector_state"], "not_applicable")
        self.assertEqual(self.state(vector)["state"], "cancelled")
        self.assertEqual(self.stored_counts(vector), (0, 0, 0))
        self.policy(job, True)
        self.assertEqual(vector_worker.run_once(self.vector, self.encoder), "completed")

    def test_vector_lock_recovery_failure_and_manual_retry(self):
        job = self.upload("recover.txt", b"public fixture deployment", "text/plain")
        self.assertEqual(worker.run_once(self.client), "completed")
        self.policy(job, True)
        vector = self.vector_job(job); self.jobs.append(vector)
        claimed = self.vector.claim()
        self.assertEqual(claimed["id"], vector["processing_job_id"])
        with worker.worker_lock(str(self.root/"vector.lock")):
            self.assertIn("worker_already_running", self.vector_cli(expected=1).stderr)
            self.assertEqual(self.state(vector)["state"], "running")
        self.vector_cli()
        self.assertEqual(self.state(vector)["state"], "interrupted")
        data(self.user.request("POST", self.job_path(vector)+"/retry"))
        missing = partial(vector_worker.request_encoding, str(self.root/"missing.sock"))
        self.assertEqual(vector_worker.run_once(self.vector, missing), "failed")
        self.assertEqual(self.state(vector)["state"], "failed")
        self.assertEqual(self.document(job)["keyword_state"], "completed")
        data(self.user.request("POST", self.job_path(vector)+"/retry"))
        self.assertIn("vector: completed", self.vector_cli().stdout)


if __name__ == "__main__":
    # Reuse setup/helpers, not the seven parser-only cases; those run separately.
    suite = unittest.TestSuite(VectorE2E(name) for name in dir(VectorE2E) if name.startswith("test_vector_"))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
