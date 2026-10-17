"""Real process/socket supervision; synthetic vectors are protocol fixtures only."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from smartdocs.embeddings import EmbeddingError
from smartdocs.vector_worker import request_encoding

CHUNKS = [{"chunk_id": "a"*64, "text": "测试"}]


class ResidentServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.resident_service"),
                             "resident supervision missing")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root/"delay").write_text("0")
        self.path = str(self.root/"encoder.sock")

    def start(self, startup=3, request=0.5):
        process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("resident_service_fixture.py")),
                                    "parent", str(self.root), str(startup), str(request)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
                if (self.root/"pid").exists():
                    try: os.kill(int((self.root/"pid").read_text()), signal.SIGKILL)
                    except ProcessLookupError: pass
        self.addCleanup(stop)
        return process

    def wait_for(self, condition):
        end = time.monotonic()+4
        while time.monotonic() < end:
            if condition(): return
            time.sleep(0.01)
        self.fail("expected process state not reached")

    def child(self):
        self.wait_for(lambda: (self.root/"pid").exists())
        return int((self.root/"pid").read_text())

    def assert_reaped(self, pid):
        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
        self.assertFalse(os.path.exists(self.path))

    def test_ready_precedes_listen_reuse_and_sigterm_reaps_child(self):
        (self.root/"delay").write_text("0.2")
        parent = self.start(request=2)
        pid = self.child()
        self.assertFalse(os.path.exists(self.path))
        self.wait_for(lambda: os.path.exists(self.path))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        first = request_encoding(self.path, CHUNKS)
        self.assertEqual(first["chunks"][0]["windows"][0]["vector"], [1.0]+[0.0]*383)
        self.assertEqual(request_encoding(self.path, CHUNKS), first)
        self.assertEqual(self.child(), pid)
        parent.terminate(); parent.wait(timeout=3)
        self.assert_reaped(pid)

    def test_startup_timeout_and_failure_never_open_listener(self):
        for mode in ("timeout", "failure"):
            with self.subTest(mode=mode):
                (self.root/"delay").write_text("5" if mode == "timeout" else "0")
                if mode == "failure": (self.root/"fail").touch()
                parent = self.start(startup=0.3)
                pid = self.child()
                self.assertEqual(parent.wait(timeout=3), 1)
                self.assert_reaped(pid)
                (self.root/"pid").unlink()

    def test_stopped_native_child_times_out_busy_is_not_queued_and_no_replay(self):
        parent = self.start(request=1)
        pid = self.child()
        self.wait_for(lambda: os.path.exists(self.path))
        os.kill(pid, signal.SIGSTOP)  # Cannot run Python signal handlers or cleanup.
        with socket.socket(socket.AF_UNIX) as first:
            first.settimeout(3); first.connect(self.path)
            first.sendall(json.dumps(CHUNKS).encode()); first.shutdown(socket.SHUT_WR)
            time.sleep(0.08)
            with socket.socket(socket.AF_UNIX) as delayed:
                delayed.settimeout(3); delayed.connect(self.path)
                time.sleep(0.15)  # Force accept before any request bytes exist.
                delayed.sendall(json.dumps(CHUNKS).encode()); delayed.shutdown(socket.SHUT_WR)
                self.assertEqual(json.loads(delayed.recv(4096)), {"error": "embedding_busy"})
            with self.assertRaises(EmbeddingError) as caught: request_encoding(self.path, CHUNKS)
            self.assertEqual(caught.exception.code, "embedding_busy")
            self.assertEqual(json.loads(first.recv(4096)), {"error": "embedding_timeout"})
        self.assertEqual(parent.wait(timeout=3), 1)
        self.assert_reaped(pid)
        self.assertFalse((self.root/"encoded").exists())

    def test_slow_client_has_total_deadline_and_invalid_input_never_encodes(self):
        parent = self.start()
        pid = self.child(); self.wait_for(lambda: os.path.exists(self.path))
        for payload in (b"{}", b"x"*65537):
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(3); conn.connect(self.path); conn.sendall(payload)
                conn.shutdown(socket.SHUT_WR)
                self.assertEqual(json.loads(conn.recv(4096)), {"error": "embedding_invalid_input"})
        self.assertFalse((self.root/"encoded").exists())
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(3); conn.connect(self.path); conn.sendall(b"[")
            self.assertEqual(json.loads(conn.recv(4096)), {"error": "embedding_timeout"})
        parent.wait(timeout=3); self.assert_reaped(pid)

    def test_total_deadline_kills_model_and_stopped_transient_tokenizer(self):
        (self.root/"tokenizer_fault").touch()
        parent = self.start(request=1)
        pid = self.child(); self.wait_for(lambda: os.path.exists(self.path))
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(4); conn.connect(self.path)
            conn.sendall(json.dumps(CHUNKS).encode()); conn.shutdown(socket.SHUT_WR)
            marker = self.root/"tokenizer_pid"
            self.wait_for(marker.exists)
            tokenizer_pid = int(marker.read_text())
            self.assertEqual(os.getpgid(tokenizer_pid), pid)
            self.assertEqual(json.loads(conn.recv(4096)), {"error": "embedding_timeout"})
        self.assertEqual(parent.wait(timeout=3), 1)
        self.assert_reaped(pid)
        self.wait_for(lambda: not Path(f"/proc/{tokenizer_pid}").exists())
        self.assertFalse((self.root/"encoded").exists())

    def test_existing_path_unsafe_directory_and_shared_lock_refused(self):
        Path(self.path).write_text("keep")
        self.assertEqual(self.start().wait(timeout=3), 1)
        self.assertEqual(Path(self.path).read_text(), "keep")
        Path(self.path).unlink()
        os.chmod(self.root, 0o755)
        self.assertEqual(self.start().wait(timeout=3), 1)
        self.assertFalse((self.root/"pid").exists())
        os.chmod(self.root, 0o700)
        from smartdocs.worker import worker_lock
        with worker_lock(str(self.root/"model.lock")):
            self.assertEqual(self.start().wait(timeout=3), 1)
        self.assertFalse((self.root/"pid").exists())

    def test_child_crash_returns_safe_failure_and_removes_listener(self):
        parent = self.start(); pid = self.child()
        self.wait_for(lambda: os.path.exists(self.path))
        os.kill(pid, signal.SIGKILL)
        # It may already have noticed idle IPC EOF and removed the listener.
        if os.path.exists(self.path):
            with self.assertRaises(EmbeddingError): request_encoding(self.path, CHUNKS)
        self.assertEqual(parent.wait(timeout=3), 1)
        self.assert_reaped(pid)

    def test_oversized_or_invalid_child_reply_retires_instance(self):
        for fault, expected in (("oversized", "embedding_result_too_large"),
                                ("malformed", "embedding_invalid_result")):
            with self.subTest(fault=fault):
                (self.root/"wire_fault").write_text(fault)
                parent = self.start(); pid = self.child()
                self.wait_for(lambda: os.path.exists(self.path))
                with self.assertRaises(EmbeddingError) as caught: request_encoding(self.path, CHUNKS)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(parent.wait(timeout=3), 1)
                self.assert_reaped(pid)
                (self.root/"pid").unlink()

    def test_entrypoint_refuses_unrestricted_network_before_loading(self):
        result = subprocess.run([sys.executable, "-B", "-m", "smartdocs.resident_service", self.path,
                                 str(self.root), sys.executable, str(self.root/"model.lock")], timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(os.path.exists(self.path))

    def test_systemd_ready_notification_follows_model_initialization(self):
        from unittest.mock import patch
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            notify.bind(str(self.root/"notify.sock")); notify.settimeout(3)
            (self.root/"delay").write_text("0.2")
            with patch.dict(os.environ, {"NOTIFY_SOCKET": str(self.root/"notify.sock")}):
                parent = self.start(request=2)
            pid = self.child()
            self.assertEqual(notify.recv(1024), b"READY=1")
            self.assertTrue(os.path.exists(self.path))
            parent.terminate(); parent.wait(timeout=3); self.assert_reaped(pid)


if __name__ == "__main__": unittest.main()
