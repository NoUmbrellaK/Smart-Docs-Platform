"""Real resident interruption after batch one; externally restart only the test encoder.

Catches partial-vector visibility, lost failures and implicit retry after restart.
Requires an isolated MySQL and the dedicated capped/offline encoder service. No mocks.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import threading
import time
import unittest

from m1_http_test import HttpClient, data, login
from m2_vector_worker_test import VectorE2E
from smartdocs import vector_worker, worker


class ResidentRecovery(VectorE2E):
    def test_stopped_request_stays_failed_until_explicit_retry(self):
        parent = int(os.environ["SMARTDOCS_TEST_ENCODER_PID"])
        unit = "smartdocs-m2-recovery-encoder.service"
        # Refuse to signal a PID outside the explicitly named disposable test unit.
        expected_cgroup = f"0::/smartdocsvectorcheck.slice/{unit}"
        self.assertEqual(Path(f"/proc/{parent}/cgroup").read_text().strip(), expected_cgroup)
        self.assertEqual(Path(f"/proc/{parent}").stat().st_uid, os.geteuid())
        children = Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
        self.assertEqual(len(children), 1)
        child = int(children[0])
        self.assertEqual(Path(f"/proc/{child}/cgroup").read_text().strip(), expected_cgroup)
        path = os.environ["SMARTDOCS_ENCODER_SOCKET"]

        with ExitStack() as cleanup:
            parent_fd = os.pidfd_open(parent)
            cleanup.callback(os.close, parent_fd)
            child_fd = os.pidfd_open(child)
            cleanup.callback(os.close, child_fd)
            # Recheck after pinning process identities; never accept a similarly named unit.
            for pid in (parent, child):
                self.assertEqual(Path(f"/proc/{pid}/cgroup").read_text().strip(), expected_cgroup)

            def stop_encoder():
                try: signal.pidfd_send_signal(parent_fd, signal.SIGTERM)
                except ProcessLookupError: pass

            cleanup.callback(stop_encoder)
            # Nine chunks require two real batches; one is staged before the fault.
            job = self.upload("recovery.txt", b"backup "*1372, "text/plain")
            self.assertEqual(worker.run_once(self.client), "completed")
            self.assertEqual(self.document(job)["chunk_count"], 9)
            self.policy(job, True)
            vector = self.vector_job(job)
            self.jobs.append(vector)
            query = "backup"
            query_embedding = self.encoder([{"chunk_id": hashlib.sha256(query.encode()).hexdigest(), "text": query}])

            def assert_unpublished():
                result = data(self.user.request("POST", f'/api/v1/projects/{self.project["id"]}/search',
                    {"mode": "hybrid", "query": query, "query_embedding": query_embedding, "page_size": 20}))
                self.assertEqual(result["retrieval"], "keyword_only")
                self.assertIn("vector_index_unavailable", result["warnings"])
                self.assertEqual(len(result["items"]), 9)
                for item in result["items"]:
                    self.assertEqual(item["version_id"], job["version_id"])
                    self.assertEqual(item["match_sources"], ["keyword"])

            paused = threading.Event()
            calls = []

            def interrupt_second_batch(chunks):
                calls.append(len(chunks))
                if len(calls) == 2:
                    signal.pidfd_send_signal(child_fd, signal.SIGSTOP)
                    paused.set()
                return self.encoder(chunks)

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(vector_worker.run_once, self.vector, interrupt_second_batch)
                try:
                    self.assertTrue(paused.wait(90), "first real batch did not complete")
                    # An invalid probe cannot occupy the model. BUSY proves the valid
                    # second-batch request was accepted, not merely queued by this test.
                    deadline = time.monotonic()+5
                    while True:
                        with socket.socket(socket.AF_UNIX) as probe:
                            probe.settimeout(2)
                            probe.connect(path)
                            probe.sendall(b"{}")
                            probe.shutdown(socket.SHUT_WR)
                            raw = bytearray()
                            while part := probe.recv(1024):
                                raw.extend(part)
                                self.assertLess(len(raw), 1024)
                        error = json.loads(raw)["error"]
                        if error == "embedding_busy": break
                        self.assertEqual(error, "embedding_invalid_input")
                        self.assertLess(time.monotonic(), deadline, "second batch was not accepted")
                        time.sleep(.05)
                    self.assertFalse(future.done())
                    staged = self.stored_counts(vector)
                    self.assertEqual(staged[:2], (1, 8))
                    self.assertGreaterEqual(staged[2], 8)
                    self.assertEqual(self.state(vector)["state"], "running")
                    self.assertEqual(self.document(job)["vector_state"], "processing")
                    assert_unpublished()
                    print(json.dumps({"stage": "inflight_second_batch", "encoder_busy": True,
                        "stored_counts": staged, "partial_vectors_visible": False}), flush=True)
                finally:
                    stop_encoder()
                self.assertEqual(future.result(timeout=90), "failed")
            self.assertEqual(calls, [8, 1])  # No automatic encoding replay.
            for fd in (parent_fd, child_fd):
                self.assertTrue(select.select([fd], [], [], 10)[0], "encoder process survived stop")
            self.assertFalse(os.path.exists(path), "stopped instance retained socket")
            failed = self.state(vector)
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["attempt_count"], 1)
            self.assertIn(failed["error_code"], ("embedding_invalid_result", "embedding_worker_failed"))
            self.assertEqual(self.stored_counts(vector), staged)
            self.assertEqual(self.document(job)["vector_state"], "failed")
            assert_unpublished()

            # Restart the actual C++ service, not just an in-memory client.
            self.server.stop()
            self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
            self.user = HttpClient(self.server.port)
            login(self.user, "worker-e2e-admin", self.password)
            self.vector = vector_worker.VectorClient(self.server.port, self.token)
            self.assertEqual(self.state(vector), failed)
            self.assertEqual(self.stored_counts(vector), staged)
            self.assertEqual(vector_worker.run_once(self.vector, self.encoder), "idle")
            assert_unpublished()
            print(json.dumps({"stage": "await_manual_encoder_restart", "persistent_state": "failed",
                "error_code": failed["error_code"], "attempt_count": 1,
                "old_parent_and_model_exited": True}), flush=True)

            # External operator must verify the old unit/cgroup is empty before starting
            # the replacement at the same private socket. No test auto-restarts services.
            deadline = time.monotonic()+240
            while not os.path.exists(path):
                self.assertLess(time.monotonic(), deadline, "manual encoder restart not observed")
                time.sleep(.2)
            self.assertEqual(vector_worker.run_once(self.vector, self.encoder), "idle")
            self.assertEqual(self.state(vector), failed)
            assert_unpublished()
            data(self.user.request("POST", self.job_path(vector)+"/retry"))
            self.assertEqual(self.state(vector)["state"], "pending")
            retry_calls = []

            def retry_encoding(chunks):
                if not retry_calls:
                    self.assertEqual(self.stored_counts(vector), (0, 0, 0), "new attempt retained partial batches")
                retry_calls.append(len(chunks))
                return self.encoder(chunks)

            self.assertEqual(vector_worker.run_once(self.vector, retry_encoding), "completed")
            self.assertEqual(retry_calls, [8, 1])
            self.assertEqual((self.state(vector)["state"], self.state(vector)["attempt_count"]), ("completed", 2))
            status = self.document(job)
            self.assertEqual((status["parse_state"], status["keyword_state"], status["vector_state"]),
                             ("completed", "completed", "completed"))
            self.assertEqual(self.stored_counts(vector)[:2], (2, 9))
            result = data(self.user.request("POST", f'/api/v1/projects/{self.project["id"]}/search',
                {"mode": "hybrid", "query": query, "query_embedding": query_embedding, "page_size": 20}))
            self.assertEqual(result["retrieval"], "hybrid")
            self.assertEqual(len(result["items"]), 9)
            self.assertEqual(len({item["chunk"]["chunk_id"] for item in result["items"]}), 9)
            for item in result["items"]:
                self.assertEqual(item["version_id"], job["version_id"])
                self.assertIn("vector", item["match_sources"])
            self.assertEqual(vector_worker.run_once(self.vector, self.encoder), "idle")
            print(json.dumps({"stage": "explicit_retry_completed", "attempt_count": 2,
                "stored_counts": self.stored_counts(vector), "retrieval": result["retrieval"],
                "same_file_version": True, "automatic_retry": False}), flush=True)


if __name__ == "__main__":
    suite = unittest.TestSuite([ResidentRecovery("test_stopped_request_stays_failed_until_explicit_retry")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
