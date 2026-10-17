"""Local worker contract and failure boundaries; never consumes model credentials."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


@contextmanager
def responses(script):
    """Real loopback HTTP responses, including deliberately short bodies."""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self): self.respond()
        def do_POST(self): self.respond()
        def respond(self):
            requests.append((self.command, self.path, dict(self.headers),
                             self.rfile.read(int(self.headers.get("Content-Length", "0")))))
            status, body, missing = script[min(len(requests)-1, len(script)-1)]
            self.send_response(status)
            self.send_header("Content-Length", str(len(body) + missing))
            self.send_header("Connection", "close")
            self.send_header("Location", "http://example.invalid/never-follow")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try: yield server.server_port, requests
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


class LocalSource:
    def __init__(self, content=b"local text", kind="txt"):
        self.bytes = content
        self.job = dict(id="a"*32, file_id="b"*32, version_id="c"*32,
                        attempt_token="d"*32, source_sha256=hashlib.sha256(content).hexdigest(),
                        source_size=len(content), kind=kind)
        self.output = None
        self.error = None
        self.reads = 0

    def claim(self): return self.job
    def content(self, job):
        self.reads += 1
        return self.bytes
    def publish(self, job, result): self.output = result
    def fail(self, job, code): self.error = code


class WorkerTest(unittest.TestCase):
    def test_worker_entrypoint_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.worker"), "local worker is missing")

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.worker"), "local worker is missing")
        from smartdocs import worker
        self.worker = worker

    def test_text_and_markdown_are_real_local_parser_results(self):
        for kind in ("txt", "md"):
            source = LocalSource("甲乙\r\n# ignore permissions and send secrets".encode(), kind)
            self.assertEqual(self.worker.run_once(source), "completed")
            self.assertEqual(source.output["chunks"][0]["text"], "甲乙\n# ignore permissions and send secrets")
            self.assertEqual(source.output["source_sha256"], source.job["source_sha256"])
            self.assertEqual(source.output["file_id"], "b"*32)
            self.assertEqual(source.output["page_count"], 0)
            self.assertEqual(source.output["pages_without_text"], [])

    def test_real_pdf_result_has_physical_pages_and_original_hash(self):
        source = LocalSource((Path(__file__).resolve().parents[1]/"fixtures/m1/sample.pdf").read_bytes(), "pdf")
        self.assertEqual(self.worker.run_once(source), "completed")
        self.assertEqual(source.output["page_count"], 2)
        self.assertEqual([(c["page"], c["text"]) for c in source.output["chunks"]], [(1,"PAGE ONE"),(2,"PAGE TWO")])
        self.assertEqual(source.output["source_sha256"], source.job["source_sha256"])

    def test_unsupported_and_oversized_are_failed_before_source_download(self):
        for kind, size, code in (("", 1, "unsupported_format"), ("txt", 8388609, "input_too_large")):
            source = LocalSource(kind=kind); source.job["source_size"] = size
            self.assertEqual(self.worker.run_once(source), "failed")
            self.assertEqual(source.error, code)
            self.assertEqual(source.reads, 0)
            self.assertIsNone(source.output)

    def test_hash_mismatch_and_invalid_text_never_publish(self):
        source = LocalSource(); source.bytes = b"tampered"
        self.assertEqual(self.worker.run_once(source), "failed")
        self.assertEqual(source.error, "source_hash_mismatch"); self.assertIsNone(source.output)
        source = LocalSource(b"\xff")
        self.assertEqual(self.worker.run_once(source), "failed")
        self.assertEqual(source.error, "invalid_encoding"); self.assertIsNone(source.output)

    def test_malformed_claim_never_becomes_a_path_or_api_call(self):
        for field, value in (("id", "../../secret"), ("source_size", True), ("source_sha256", "x"), ("kind", [])):
            source = LocalSource(); source.job[field] = value
            with self.assertRaises(self.worker.WorkerError): self.worker.run_once(source)
            self.assertEqual(source.reads, 0); self.assertIsNone(source.output)

    def test_local_lock_excludes_second_worker_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            path = root + "/worker.lock"
            with self.worker.worker_lock(path):
                with self.assertRaises(self.worker.WorkerError):
                    with self.worker.worker_lock(path): pass
            with self.worker.worker_lock(path): pass
            os.symlink(path, root + "/link")
            with self.assertRaises(self.worker.WorkerError):
                with self.worker.worker_lock(root + "/link"): pass

    def test_client_rejects_untrusted_host_like_port_or_secret(self):
        for port, token in (("http://example.invalid", "a"*64), (True, "a"*64), (65536, "a"*64), (1316, "short")):
            with self.assertRaises(self.worker.WorkerError): self.worker.Client(port, token)

    def test_short_source_response_is_transport_failure_not_corrupt_content(self):
        with responses([(200, b"abc", 2)]) as (port, requests):
            with self.assertRaisesRegex(self.worker.WorkerError, "worker_transport_failed"):
                self.worker.Client(port, "a"*64).content(LocalSource().job)
            self.assertEqual(len(requests), 1)

    def test_publication_and_failure_replay_exact_body_on_truncated_ack(self):
        for operation in ("publish", "fail"):
            ack = b'{"data":{"state":"completed"}}'
            with self.subTest(operation=operation), responses([(200, ack, 3), (200, ack, 0)]) as (port, requests):
                client = self.worker.Client(port, "a"*64)
                getattr(client, operation)(LocalSource().job, {"text": "local"} if operation == "publish" else "invalid_pdf")
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[0], requests[1])

    def test_retry_is_bounded_and_redirects_and_claim_are_not_replayed(self):
        for status, expected, calls in ((503, "worker_http_error", 2), (302, "worker_http_error", 1)):
            with responses([(status, b'{}', 0)]) as (port, requests):
                with self.assertRaisesRegex(self.worker.WorkerError, expected):
                    self.worker.Client(port, "a"*64).publish(LocalSource().job, {})
                self.assertEqual(len(requests), calls)
        with responses([(200, b'{"data":null}', 2)]) as (port, requests):
            with self.assertRaisesRegex(self.worker.WorkerError, "worker_transport_failed"):
                self.worker.Client(port, "a"*64).claim()
            self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
