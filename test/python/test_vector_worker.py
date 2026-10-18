"""HTTP and Unix-stream protocol checks; unit vectors are fixtures, not model quality."""
import hashlib
import importlib
import importlib.util
import io
import inspect
import json
import os
import socket
import tempfile
import threading
import time
import unittest

from smartdocs.embeddings import EMBEDDING_REVISION, EmbeddingError
from smartdocs.worker import WorkerError
from test_worker import responses


JOB = dict(id="a"*32, file_id="b"*32, version_id="c"*32, attempt_token="d"*32)
CHUNKS = [{"chunk_id": "%064x" % n, "text": "中文🙂"} for n in range(9)]


def encoded(chunks):
    return {"embedding_revision": EMBEDDING_REVISION, "chunks": [dict(
        chunk_id=c["chunk_id"], text_sha256=hashlib.sha256(c["text"].encode()).hexdigest(),
        windows=[dict(char_start=0, char_end=len(c["text"]), vector=[1.0]+[0.0]*383)]) for c in chunks]}


def reply(data):
    return 200, json.dumps({"data": data}, ensure_ascii=False).encode(), 0


def batch(start=0, total=9):
    return dict(batch_start=start, chunk_count=total, embedding_revision=EMBEDDING_REVISION,
                chunks=CHUNKS[start:min(start+8, total)])


class VectorWorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.vector_worker"), "vector scheduler missing")
        self.m = importlib.import_module("smartdocs.vector_worker")

    def test_two_batches_replay_same_publication_then_finish_only_on_final_ack(self):
        ack = reply({"state": "running"})
        script = [reply(JOB), reply(batch()), (200, ack[1], 3), ack,
                  reply(batch(8)), reply({"state": "completed"})]
        with responses(script) as (port, calls):
            self.assertEqual(self.m.run_once(self.m.VectorClient(port, "e"*64), encoded), "completed")
        self.assertEqual([c[1] for c in calls], ["/internal/v1/processing" + p for p in (
            "/vector/claim", "/vector/"+JOB["id"]+"/chunks", "/vector/"+JOB["id"]+"/result",
            "/vector/"+JOB["id"]+"/result", "/vector/"+JOB["id"]+"/chunks", "/vector/"+JOB["id"]+"/result")])
        self.assertEqual(calls[2], calls[3])
        self.assertEqual(json.loads(calls[5][3])["batch_start"], 8)
        self.assertEqual(len(json.loads(calls[2][3])["result"]["chunks"]), 8)
        self.assertEqual(calls[2][2]["X-SmartDocs-Attempt"], "d"*32)

    def test_idle_and_malformed_claim_never_encode_or_fetch(self):
        for value in (None, {**JOB, "id": "../../secret"}):
            with responses([reply(value)]) as (port, calls):
                client = self.m.VectorClient(port, "e"*64)
                if value is None:
                    self.assertEqual(self.m.run_once(client, lambda _: self.fail("must not encode")), "idle")
                else:
                    with self.assertRaises(WorkerError): self.m.run_once(client, encoded)
                self.assertEqual(len(calls), 1)

    def test_bad_batch_fails_before_encoding_or_publication(self):
        for changes in ({"batch_start": 8}, {"chunk_count": True}, {"chunk_count": 2001},
                        {"embedding_revision": "wrong"}, {"chunks": CHUNKS[:1]}):
            with responses([reply(JOB), reply({**batch(), **changes}), reply({"state": "failed"})]) as (port, calls):
                self.assertEqual(self.m.run_once(self.m.VectorClient(port, "e"*64),
                                               lambda _: self.fail("must not encode")), "failed")
            self.assertEqual(json.loads(calls[-1][3]), {"error_code": "embedding_invalid_input"})
            self.assertTrue(calls[-1][1].endswith("/fail"))

    def test_encoder_failure_and_invalid_result_do_not_publish(self):
        def timeout(_): raise EmbeddingError("embedding_timeout")
        for encoder, code in ((timeout, "embedding_timeout"), (lambda _: {}, "embedding_invalid_result")):
            with responses([reply(JOB), reply(batch()), reply({"state": "failed"})]) as (port, calls):
                self.assertEqual(self.m.run_once(self.m.VectorClient(port, "e"*64), encoder), "failed")
            self.assertEqual(json.loads(calls[-1][3]), {"error_code": code})

    def test_revoked_attempt_stops_before_next_batch_and_transport_uncertainty_exits(self):
        revoked = (409, b'{"error":{"code":"attempt_conflict"}}', 0)
        with responses([reply(JOB), reply(batch()), revoked]) as (port, calls):
            self.assertEqual(self.m.run_once(self.m.VectorClient(port, "e"*64), encoded), "discarded")
            self.assertEqual(len(calls), 3)
        with responses([reply(JOB), reply(batch()), (503, b'{}', 0)]) as (port, calls):
            with self.assertRaises(WorkerError): self.m.run_once(self.m.VectorClient(port, "e"*64), encoded)
            self.assertEqual(len(calls), 4)  # only identical publication replay, no new claim/fail

    def test_misleading_ack_exits_without_overwriting_uncertain_publication(self):
        with responses([reply(JOB), reply(batch()), reply({"state": "completed"})]) as (port, calls):
            with self.assertRaises(WorkerError):
                self.m.run_once(self.m.VectorClient(port, "e"*64), encoded)
            self.assertEqual(len(calls), 3)

    def test_changed_batch_total_fails_before_more_encoding(self):
        with responses([reply(JOB), reply(batch()), reply({"state": "running"}),
                        reply(batch(8, 8)), reply({"state": "failed"})]) as (port, calls):
            self.assertEqual(self.m.run_once(self.m.VectorClient(port, "e"*64), encoded), "failed")
        self.assertTrue(calls[-1][1].endswith("/fail"))

    def test_invalid_failure_ack_also_exits(self):
        def broken(_): raise EmbeddingError("embedding_timeout")
        with responses([reply(JOB), reply(batch()), reply({})]) as (port, calls):
            with self.assertRaises(WorkerError):
                self.m.run_once(self.m.VectorClient(port, "e"*64), broken)
            self.assertEqual(len(calls), 3)

    def test_socket_exchange_validates_results_limits_and_safe_errors(self):
        for output, expected in ((encoded(CHUNKS[:1]), None),
                                 ({"error": "embedding_timeout"}, "embedding_timeout"),
                                 ({"error": "secret document"}, "embedding_worker_failed"),
                                 ({}, "embedding_invalid_result"),
                                 ("x"*(2*1024*1024+1), "embedding_result_too_large")):
            with tempfile.TemporaryDirectory() as tmp:
                path = tmp+"/encoder.sock"
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                    server.bind(path); os.chmod(path, 0o600); server.listen(1)
                    received = []
                    def serve():
                        with server.accept()[0] as conn:
                            raw = bytearray()
                            while part := conn.recv(4096): raw.extend(part)
                            received.append(json.loads(raw))
                            try: conn.sendall(json.dumps(output).encode())
                            except BrokenPipeError: pass
                    thread = threading.Thread(target=serve); thread.start()
                    try:
                        if expected:
                            with self.assertRaises(EmbeddingError) as caught:
                                self.m.request_encoding(path, CHUNKS[:1])
                            self.assertEqual(caught.exception.code, expected)
                        else:
                            self.assertEqual(self.m.request_encoding(path, CHUNKS[:1]), output)
                    finally: thread.join(3)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(received, [CHUNKS[:1]])

    def test_unsafe_socket_path_refused_without_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp+"/socket"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path); os.chmod(path, 0o666)
                for target in (path, "relative", tmp+"/missing"):
                    with self.assertRaises(EmbeddingError): self.m.request_encoding(target, CHUNKS[:1])
                os.chmod(path, 0o600); os.symlink(path, tmp+"/link")
                with self.assertRaises(EmbeddingError): self.m.request_encoding(tmp+"/link", CHUNKS[:1])

    def test_short_query_deadline_closes_hung_socket_without_retry(self):
        self.assertIn("timeout",inspect.signature(self.m.request_encoding).parameters,
                      "query encoding cannot use a shorter deadline")
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+"/query.sock"
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
                server.bind(path); os.chmod(path,0o600); server.listen(1)
                received=[]; released=threading.Event()
                def serve():
                    with server.accept()[0] as conn:
                        raw=bytearray()
                        while part:=conn.recv(4096): raw.extend(part)
                        received.append(json.loads(raw)); released.wait(2)
                thread=threading.Thread(target=serve); thread.start()
                start=time.monotonic()
                try:
                    with self.assertRaises(EmbeddingError) as caught:
                        self.m.request_encoding(path,CHUNKS[:1],timeout=0.05)
                    self.assertEqual(caught.exception.code,"embedding_timeout")
                    self.assertLess(time.monotonic()-start,1)
                finally: released.set(); thread.join(3)
                self.assertFalse(thread.is_alive()); self.assertEqual(received,[CHUNKS[:1]])
                for timeout in (0,-1,76,True,float("nan"),float("inf")):
                    with self.assertRaises(EmbeddingError):
                        self.m.request_encoding(path,CHUNKS[:1],timeout=timeout)

    def test_elapsed_connect_deadline_prevents_sending_query(self):
        from unittest.mock import patch
        original=socket.socket
        class DelayedConnect(original):
            def connect(self,address):
                super().connect(address)
                time.sleep(0.08)  # OS connection succeeded, but caller resumed after the deadline.
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+"/query.sock"
            with original(socket.AF_UNIX,socket.SOCK_STREAM) as server:
                server.bind(path); os.chmod(path,0o600); server.listen(1)
                received=[]
                def serve():
                    with server.accept()[0] as conn:
                        raw=bytearray()
                        while part:=conn.recv(4096): raw.extend(part)
                        received.append(bytes(raw))
                thread=threading.Thread(target=serve); thread.start()
                try:
                    with patch.object(self.m.socket,"socket",DelayedConnect):
                        with self.assertRaises(EmbeddingError) as caught:
                            self.m.request_encoding(path,CHUNKS[:1],timeout=0.05)
                        self.assertEqual(caught.exception.code,"embedding_timeout")
                finally: thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(received,[b""],"expired connection must not send query bytes")


class EncodingServiceTests(unittest.TestCase):
    def test_entrypoint_refuses_missing_kernel_network_restriction(self):
        from unittest.mock import patch
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.embedding_service"))
        service = importlib.import_module("smartdocs.embedding_service")
        class Stream:
            buffer = io.BytesIO()
        with patch.object(service.os, "geteuid", return_value=65534), \
             patch.object(service.sys, "argv", ["encoder", "/model", "/python", "/lock"]), \
             patch.object(service.sys, "stdout", Stream()), \
             patch.object(service.sys, "stdin", Stream()):
            self.assertEqual(service.main(), 1)

    def test_stdio_service_bounds_input_and_sanitizes_errors(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.embedding_service"), "stdio encoder service missing")
        service = importlib.import_module("smartdocs.embedding_service")
        for payload, expected in ((json.dumps(CHUNKS[:1]).encode(), encoded(CHUNKS[:1])),
                                  (b"x"*65537, {"error": "embedding_invalid_input"}),
                                  (b'{}', {"error": "embedding_invalid_input"})):
            output = io.BytesIO()
            service.handle_request(io.BytesIO(payload), output, encoded)
            self.assertEqual(json.loads(output.getvalue()), expected)
        def broken(_): raise RuntimeError("private text")
        output = io.BytesIO()
        service.handle_request(io.BytesIO(json.dumps(CHUNKS[:1]).encode()), output, broken)
        self.assertEqual(json.loads(output.getvalue()), {"error": "embedding_worker_failed"})
