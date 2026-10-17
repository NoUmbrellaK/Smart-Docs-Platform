"""Real loopback HTTP boundary; only expensive model and C++/DB are test doubles."""
import contextlib
import http.client
import importlib
import importlib.util
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from smartdocs.embeddings import EmbeddingError


PROJECT = "a" * 32
PATH = f"/api/v1/projects/{PROJECT}/hybrid-search"
ORIGIN = "https://docs.example.test"
COOKIE = "smartdocs_session=" + "b" * 64


@contextlib.contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class SearchHttpTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.search_http"),
                             "same-origin hybrid search HTTP gateway not implemented")
        self.module = importlib.import_module("smartdocs.search_http")
        self.calls = []
        self.encoded = 0
        self.statuses = [200, 200]
        self.bad_backend = None
        owner = self

        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append((self.path, dict(self.headers), body))
                status = owner.statuses[min(len(owner.calls)-1, len(owner.statuses)-1)]
                response = (owner.bad_backend if owner.bad_backend is not None else
                    json.dumps({"data": {"items": [], "retrieval": "keyword_only"}}
                        if status == 200 else {"error": {"code": "forbidden",
                            "message": "secret upstream internals"}}).encode())
                self.send_response(status)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        backend_port = self.stack.enter_context(running(HTTPServer(("127.0.0.1", 0), Backend)))
        self.gateway = self.module.SearchHTTPServer(0, backend_port, ORIGIN, self.encode)
        self.port = self.stack.enter_context(running(self.gateway))

    def encode(self, chunks):
        self.encoded += 1
        raise EmbeddingError("embedding_timeout")

    def request(self, body=b'{"query":"restore"}', *, path=PATH, headers=None, method="POST"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        values = {"Host": "docs.example.test", "Origin": ORIGIN,
                  "Cookie": COOKIE, "Content-Type": "application/json"}
        values.update(headers or {})
        try:
            connection.request(method, path, body, values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_degraded_query_forwards_only_session_and_fixed_origin_and_rechecks(self):
        status, headers, body = self.request(headers={"Cookie": COOKIE + "; other=private",
            "Authorization": "Bearer not-forwarded", "X-Forwarded-Host": "evil.test"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data"]["retrieval"], "keyword_only")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Connection"], "close")
        self.assertEqual(self.encoded, 1)
        self.assertEqual(len(self.calls), 2)
        for path, forwarded, body in self.calls:
            self.assertEqual(path, f"/api/v1/projects/{PROJECT}/search")
            self.assertEqual(forwarded["Cookie"], COOKIE)
            self.assertEqual(forwarded["Host"], "docs.example.test")
            self.assertEqual(forwarded["Origin"], ORIGIN)
            self.assertNotIn("Authorization", forwarded)
            self.assertNotIn("X-Forwarded-Host", forwarded)
            self.assertEqual(body, {"mode": "hybrid", "query": "restore"})

    def test_bad_origin_host_cookie_never_reaches_backend_or_encoder(self):
        for headers, expected in [({"Origin": "https://evil.test"}, 403),
                ({"Host": "evil.test"}, 403), ({"Origin": "null"}, 403),
                ({"Cookie": ""}, 401), ({"Cookie": COOKIE + "; " + COOKIE}, 401)]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers=headers)[0], expected)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.encoded, 0)

    def test_denied_backend_never_encodes_and_does_not_leak_error(self):
        self.statuses = [403]
        status, _, body = self.request()
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "forbidden")
        self.assertNotIn(b"secret", body)
        self.assertEqual(self.encoded, 0)

    def test_revocation_after_encoding_returns_error_not_stale_preflight(self):
        self.statuses = [200, 401]
        status, _, body = self.request()
        self.assertEqual(status, 401)
        self.assertNotIn(b'"data"', body)
        self.assertEqual(self.encoded, 1)

    def test_rejects_invalid_body_identity_mode_vectors_and_duplicate_keys(self):
        for body in [b"[]", b'{"query":"x","mode":"keyword"}',
                b'{"query":"x","user_id":"fake"}', b'{"query":"x","query_embedding":{}}',
                b'{"query":"x","query":"y"}', b'{"query":NaN}', b'{"query":"\\ud800"}',
                b"["*1500, b'{"query":3}', b'{}']:
            with self.subTest(body=body[:80]):
                self.assertEqual(self.request(body)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.encoded, 0)

    def test_limits_body_route_and_media_type(self):
        self.assertEqual(self.request(b"x"*4097)[0], 413)
        self.assertEqual(self.request(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request(path=PATH+"?url=http://evil")[0], 404)
        self.assertEqual(self.request(path="/api/v1/projects/../hybrid-search")[0], 404)
        self.assertEqual(self.calls, [])

    def test_rejects_ambiguous_http_framing_and_duplicate_origin(self):
        for extra in ["Content-Length: 2\r\n", "Transfer-Encoding: chunked\r\n",
                      "Origin: https://evil.test\r\n", "Host: evil.test\r\n"]:
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as client:
                client.sendall((f"POST {PATH} HTTP/1.1\r\nHost: docs.example.test\r\n"
                    f"Origin: {ORIGIN}\r\nCookie: {COOKIE}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: 2\r\n{extra}\r\n{{}}").encode())
                self.assertIn(b" 400 ", client.recv(4096).split(b"\r\n")[0])
        self.assertEqual(self.calls, [])

    def test_malformed_oversized_or_redirected_backend_is_sanitized_unavailable(self):
        for payload, status in [(b"secret", 200), (b"x"*(256*1024+1), 200), (b"", 302)]:
            self.bad_backend = payload
            self.statuses = [status]
            code, _, body = self.request()
            self.assertEqual(code, 503)
            self.assertEqual(json.loads(body)["error"]["code"], "search_backend_unavailable")
            self.assertNotIn(b"secret", body)
        self.assertEqual(self.encoded, 0)

    def test_two_slow_connections_bound_threads_and_third_is_rejected(self):
        clients = []
        try:
            for _ in range(2):
                client = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                clients.append(client)
                client.sendall(b"POST ")
            # Ordered accept loop admits the first two sockets before this request.
            status, _, body = self.request()
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["error"]["code"], "search_busy")
        finally:
            for client in clients: client.close()

    def test_config_disallows_remote_plaintext_and_untrusted_origin_components(self):
        for origin in ["http://docs.example.test", "https://docs.example.test/", "https://u:p@host",
                       "https://docs.example.test?x", "https://docs.example.test\r\nX:evil"]:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.module.SearchHTTPServer(0, 1316, origin, self.encode)


if __name__ == "__main__": unittest.main()
