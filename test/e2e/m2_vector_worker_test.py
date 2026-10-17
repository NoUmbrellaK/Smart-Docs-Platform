"""Real C++/MySQL + real offline encoder socket; requires the capped test environment."""
from functools import partial
import os
import subprocess
import sys
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
