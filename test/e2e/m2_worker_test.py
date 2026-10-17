#!/usr/bin/env python3
"""Real sockets, real uploads and real Python worker against an isolated MySQL only."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import unittest

from m1_http_test import (HttpClient, ServerProcess, complete, create_upload,
                          create_user, data, login, upload_file, upload_parts)
from smartdocs import worker

ROOT = Path(__file__).resolve().parents[2]


class WorkerE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socket = os.environ.get("SMARTDOCS_MYSQL_SOCKET", "")
        if not re.fullmatch(r"/tmp/smartdocs-mysql\.[^/]+/mysql.sock", socket):
            raise RuntimeError("isolated MySQL test socket required")
        cls.temp = tempfile.TemporaryDirectory(prefix="smartdocs-worker-e2e.")
        cls.root = Path(cls.temp.name)
        for name in ("storage", "storage/objects", "storage/staging"):
            (cls.root/name).mkdir(mode=0o700)
        os.environ.update(SMARTDOCS_STORAGE_ROOT=str(cls.root/"storage"),
                          SMARTDOCS_PASSWORD_ITERATIONS="1000", SMARTDOCS_THREAD_COUNT="2",
                          SMARTDOCS_MYSQL_POOL_SIZE="4", SMARTDOCS_MAX_FILE_BYTES="16777216",
                          SMARTDOCS_CHUNK_BYTES="65536", SMARTDOCS_MAX_JSON_BYTES="1048576",
                          SMARTDOCS_LISTEN_ADDRESS="127.0.0.1", SMARTDOCS_SECURE_COOKIE="false")
        cls.token = secrets.token_hex(32)
        cls.password = "worker-e2e-only-password"
        create_user(str(ROOT/"bin/smartdocs-admin"), "worker-e2e-admin", cls.password)
        cls.server = ServerProcess(ROOT/"bin/server", cls.root/"logs")
        cls.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": cls.token})

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        for log in (cls.root/"logs").glob("*.log"):
            if cls.token in log.read_text(errors="replace"):
                raise AssertionError("worker credential leaked into server log")
        cls.temp.cleanup()

    def setUp(self):
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        self.project = data(self.user.request("POST", "/api/v1/projects", {"name": "Worker " + secrets.token_hex(4)}), 201)
        self.jobs = []
        self.client = worker.Client(self.server.port, self.token)

    def tearDown(self):
        for job in self.jobs:
            state = self.state(job)
            if state["state"] in ("pending", "running"):
                data(self.user.request("POST", self.job_path(job) + "/cancel"))

    def job_path(self, job): return f'/api/v1/projects/{self.project["id"]}/processing-jobs/{job["processing_job_id"]}'
    def version_path(self, job): return f'/api/v1/projects/{self.project["id"]}/files/{job["file_id"]}/versions/{job["version_id"]}'
    def state(self, job): return data(self.user.request("GET", self.job_path(job)))
    def document(self, job): return data(self.user.request("GET", self.version_path(job) + "/document"))

    def upload(self, name, body, mime):
        _, result = upload_file(self.user, self.project["id"], self.project["root_directory_id"], name, body, mime)
        self.jobs.append(result)
        return result

    def cli(self, *, expected=0):
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(ROOT/"python"),
                       "SMARTDOCS_WORKER_TOKEN": self.token, "SMARTDOCS_WORKER_PORT": str(self.server.port),
                       "SMARTDOCS_WORKER_LOCK_PATH": str(self.root/"worker.lock")}
        result = subprocess.run([sys.executable, "-B", "-m", "smartdocs.worker", "--once"],
                                env=environment, capture_output=True, text=True, timeout=90)
        self.assertNotIn(self.token, result.stdout + result.stderr)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_three_formats_publish_real_chunks_and_index(self):
        examples = [("hello.txt", "Hello 文档\n".encode(), "text/plain", "utf8-lines-v1", "Hello 文档\n", 0),
                    ("guide.md", b"# Setup\nOnly local.\n", "application/octet-stream", "utf8-lines-v1", "# Setup\nOnly local.\n", 0),
                    ("pages.pdf", (ROOT/"test/fixtures/m1/sample.pdf").read_bytes(), "application/pdf", "pypdf-6.21.0-lines-v1", "PAGE ONE", 1)]
        for name, content, mime, revision, text, page in examples:
            with self.subTest(format=name):
                job = self.upload(name, content, mime)
                self.assertIn("worker: completed", self.cli().stdout)
                self.assertEqual(self.state(job)["state"], "completed")
                status = self.document(job)
                self.assertEqual((status["parse_state"], status["keyword_state"], status["vector_state"]),
                                 ("completed", "completed", "not_applicable"))
                identity = [revision, job["file_id"], job["version_id"], hashlib.sha256(content).hexdigest()]
                if page: identity.append(page)
                identity.extend([0, len(text)])
                chunk = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
                source = data(self.user.request("GET", self.version_path(job) + "/chunks/" + chunk))
                self.assertEqual(source["chunk"]["text"], text)
                self.assertEqual(source["version_id"], job["version_id"])
                if page: self.assertEqual(source["chunk"]["page"], 1)
                query = "文档" if name == "hello.txt" else "Setup" if name == "guide.md" else "PAGE"
                results = data(self.user.request("POST", f'/api/v1/projects/{self.project["id"]}/search',
                                                {"mode": "keyword", "query": query}))
                hits = [hit for hit in results["items"] if hit["file_id"] == job["file_id"]]
                self.assertTrue(hits)
                self.assertTrue(any(hit["chunk"]["chunk_id"] == chunk for hit in hits))
                self.assertEqual(results["retrieval"], "keyword_only")
                if name == "hello.txt":
                    sql = "SELECT COUNT(*) FROM document_chunks WHERE version_id='" + job["version_id"] + "' AND MATCH(body) AGAINST('文档' IN BOOLEAN MODE)"
                    query = subprocess.run(["mysql", "--protocol=SOCKET", "--socket="+os.environ["SMARTDOCS_MYSQL_SOCKET"],
                                            "--user=root", "--batch", "--skip-column-names", "smart_docs_test", "--execute", sql],
                                           capture_output=True, text=True, check=True)
                    self.assertEqual(query.stdout.strip(), "1")

    def test_large_result_uses_worker_limit_without_raising_user_limit(self):
        content = ("甲乙" * 300000).encode()
        job = self.upload("larger.txt", content, "text/plain")
        self.assertIn("worker: completed", self.cli().stdout)
        status = self.document(job)
        self.assertEqual(status["char_count"], 600000)
        self.assertEqual(status["chunk_count"], 500)
        chunk_ids = set()
        for page in (1, 2):
            found = data(self.user.request("POST", f'/api/v1/projects/{self.project["id"]}/search',
                {"mode": "keyword", "query": "甲乙", "page": page, "page_size": 20}))
            self.assertEqual(len(found["items"]), 20)
            self.assertTrue(found["has_more"])
            self.assertEqual(found["next_page"], page + 1)
            for item in found["items"]:
                self.assertEqual(item["file_id"], job["file_id"])
                self.assertNotIn(item["chunk"]["chunk_id"], chunk_ids)
                chunk_ids.add(item["chunk"]["chunk_id"])
        self.assertEqual(len(chunk_ids), 40)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.putrequest("POST", "/api/v1/projects")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "1048580")
            connection.putheader("Cookie", self.user._cookie_header())
            connection.putheader("Origin", f"http://127.0.0.1:{self.server.port}")
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413); response.read()
        finally: connection.close()

    def test_unsupported_and_broken_files_remain_managed_but_not_indexed(self):
        for name, body, mime, expected in (("image.png", b"PNG", "image/png", "unsupported_format"),
            ("broken.pdf", b"%PDF-1.4\nbroken", "application/pdf", "invalid_pdf"),
            ("blank.txt", b" \n", "text/plain", "empty_text")):
            job = self.upload(name, body, mime)
            self.assertIn("worker: failed", self.cli().stdout)
            self.assertEqual(self.state(job)["error_code"], expected)
            self.assertEqual(self.document(job)["keyword_state"], "pending")
            self.assertEqual(self.document(job)["chunk_count"], 0)
            download = self.user.request("GET", self.version_path(job) + "/content")
            self.assertEqual(download.status, 200)
            self.assertEqual(download.body, body)
            found = data(self.user.request("POST", f'/api/v1/projects/{self.project["id"]}/search',
                                           {"mode": "metadata", "query": name}))
            self.assertEqual(len(found["items"]), 1)
            self.assertEqual(found["items"][0]["parse_state"], "failed")
            self.assertNotIn("chunk", found["items"][0])

    def test_worker_startup_recovers_only_after_acquiring_singleton_lock(self):
        job = self.upload("restart.txt", b"restart", "text/plain")
        old = self.client.claim()
        with worker.worker_lock(str(self.root/"worker.lock")):
            self.assertIn("worker_already_running", self.cli(expected=1).stderr)
            self.assertEqual(self.state(job)["state"], "running")
        self.assertIn("interrupted_previous_attempts", self.cli().stdout)
        self.assertEqual(self.state(job)["state"], "interrupted")
        data(self.user.request("POST", self.job_path(job) + "/retry"))
        self.cli()
        self.assertEqual(self.state(job)["state"], "completed")
        with self.assertRaises(worker.RemoteError) as caught: self.client.content(old)
        self.assertEqual(str(caught.exception), "attempt_conflict")

    def test_cancelled_and_superseded_attempts_cannot_block_new_version(self):
        old = self.upload("version.txt", b"old", "text/plain")
        claim = self.client.claim()
        data(self.user.request("POST", self.job_path(old) + "/cancel"))
        with self.assertRaises(worker.RemoteError): self.client.content(claim)
        data(self.user.request("POST", self.job_path(old) + "/retry"))
        claim = self.client.claim()
        content = b"new"
        task = create_upload(self.user, self.project["id"], self.project["root_directory_id"], "version.txt", content,
                             "text/plain", old["file_id"], old["version_id"])
        upload_parts(self.user, self.project["id"], task, content)
        current = data(complete(self.user, self.project["id"], task["task_id"]))
        self.jobs.append(current)
        client = self.client
        class AlreadyClaimed:
            def claim(self): return claim
            def content(self, job): return client.content(job)
            def fail(self, job, code): return client.fail(job, code)
        self.assertEqual(worker.run_once(AlreadyClaimed()), "failed")
        self.assertEqual(self.state(old)["error_code"], "version_conflict")
        self.cli()
        self.assertEqual(self.state(current)["state"], "completed")
        self.assertEqual(self.document(old)["chunk_count"], 0)

    def test_cpp_restart_requires_manual_retry(self):
        job = self.upload("server-restart.txt", b"restart server", "text/plain")
        self.client.claim()
        self.server.stop(); self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port); login(self.user, "worker-e2e-admin", self.password)
        self.assertEqual(self.state(job)["state"], "interrupted")
        data(self.user.request("POST", self.job_path(job) + "/retry"))
        self.cli()
        self.assertEqual(self.state(job)["state"], "completed")

    def test_browser_or_missing_worker_credentials_are_rejected(self):
        response = self.user.request("POST", "/internal/v1/processing/claim", headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(response.status, 401)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request("POST", "/internal/v1/processing/claim")
            response = connection.getresponse(); self.assertEqual(response.status, 401); response.read()
        finally: connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
