"""Private loopback adapter behind the same-origin reverse proxy, not a public web server."""
import argparse
from functools import partial
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
import resource
from socketserver import ThreadingMixIn
import threading
from urllib.parse import urlsplit

from .search import search_documents
from .vector_worker import request_encoding


class SearchError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate key")
        result[key] = value
    return result


def strict_json(raw):
    def invalid_constant(value): raise ValueError("nonfinite JSON")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def backend_search(port, origin, cookie, project, arguments):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        connection.request("POST", f"/api/v1/projects/{project}/search",
            json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            {"Host": urlsplit(origin).netloc, "Origin": origin, "Cookie": cookie,
             "Content-Type": "application/json", "Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            # Do not expose upstream error messages, redirects or arbitrary response bodies.
            codes = {400: "invalid_request", 401: "authentication_required", 403: "forbidden",
                     404: "not_found", 409: "conflict", 429: "rate_limited"}
            raise SearchError(response.status if response.status in codes else 503,
                              codes.get(response.status, "search_backend_unavailable"))
        raw = response.read(256*1024+1)
        if len(raw) > 256*1024: raise ValueError("response too large")
        payload = strict_json(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ValueError("invalid response")
        return payload["data"]
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        raise SearchError(503, "search_backend_unavailable") from None
    finally:
        connection.close()


class SearchHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    request_queue_size = 2
    busy_response = b'{"error":{"code":"search_busy","message":"Search busy; retry later.","retryable":true}}'

    def __init__(self, port, backend_port, origin, encoder, *, handler=None):
        parsed = urlsplit(origin)
        if (not re.fullmatch(r"https?://[a-z0-9.-]+(?::[1-9][0-9]{0,4})?", origin)
                or (parsed.scheme != "https" and parsed.hostname != "127.0.0.1")
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)
                or type(backend_port) is not int or not 1 <= backend_port <= 65535):
            raise ValueError("invalid gateway configuration")
        self.origin, self.host = origin, parsed.netloc
        self.backend_port, self.encoder = backend_port, encoder
        # ponytail: two requests, no model/result cache; scale only after measuring this ECS.
        self.slots = threading.BoundedSemaphore(2)
        super().__init__(("127.0.0.1", port), handler or SearchHandler)

    def process_request(self, request, client_address):
        request.settimeout(5)
        if not self.slots.acquire(blocking=False):
            try:
                body = self.busy_response
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                    b"Cache-Control: no-store\r\nContent-Type: application/json\r\nContent-Length: "
                    +str(len(body)).encode()+b"\r\n\r\n"+body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # No request, query, cookies or upstream exception details in logs.


class SearchHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args): pass

    def reply(self, status, payload):
        self.close_connection = True
        raw = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
        self.send_response_only(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {"error": {"code": "invalid_request", "message": "Invalid HTTP request.",
                                    "retryable": False}})

    def read_command(self, pattern):
        """Shared private gateway framing; fixed route and body semantics belong to each handler."""
        for name in ("Host", "Origin", "Cookie", "Content-Length", "Content-Type"):
            if len(self.headers.get_all(name, [])) > 1: raise SearchError(400, "invalid_request")
        if "Transfer-Encoding" in self.headers: raise SearchError(400, "invalid_request")
        route = re.fullmatch(pattern, self.path)
        if not route: raise SearchError(404, "not_found")
        if self.headers.get("Host") != self.server.host or self.headers.get("Origin") != self.server.origin:
            raise SearchError(403, "origin_mismatch")
        cookies = [pair.strip() for pair in self.headers.get("Cookie", "").split(";")
                   if pair.strip().partition("=")[0] == "smartdocs_session"]
        if len(cookies) != 1 or not re.fullmatch(r"smartdocs_session=[0-9a-f]{64}", cookies[0]):
            raise SearchError(401, "authentication_required")
        if self.headers.get("Content-Type", "").lower() not in ("application/json", "application/json; charset=utf-8"):
            raise SearchError(415, "unsupported_media_type")
        length = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,10}", length): raise SearchError(400, "invalid_request")
        length = int(length)
        if length > 4096: raise SearchError(413, "request_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length: raise SearchError(400, "invalid_request")
        return route, cookies[0], strict_json(raw)

    def do_POST(self):
        try:
            route, cookie, arguments = self.read_command(r"/api/v1/projects/([0-9a-f]{32})/hybrid-search")
            if not isinstance(arguments, dict) or not isinstance(arguments.get("query"), str):
                raise ValueError("invalid query")
            arguments["query"].encode("utf-8")  # Reject lone JSON surrogates before contacting C++.
            backend = partial(backend_search, self.server.backend_port, self.server.origin,
                              cookie, route[1])
            data = search_documents(backend, self.server.encoder, arguments)
            self.reply(200, {"data": data})
        except SearchError as error:
            self.reply(error.status, {"error": {"code": error.code,
                "message": "Search request failed. Check access or retry later.",
                "retryable": error.status in (429, 503)}})
        except (ValueError, UnicodeError, RecursionError):
            self.send_error(400)
        except TimeoutError:
            self.send_error(408)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=1317)
    parser.add_argument("--backend-port", type=int, default=1316)
    args = parser.parse_args()
    if os.geteuid() == 0: parser.error("run as the unprivileged encoder account")
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    origin = os.environ.get("SMARTDOCS_PUBLIC_ORIGIN", "")
    encoder = partial(request_encoding, os.environ.get("SMARTDOCS_ENCODER_SOCKET", "/run/smartdocs-encoder/encoder.sock"))
    with SearchHTTPServer(args.port, args.backend_port, origin, encoder) as server:
        server.serve_forever()


if __name__ == "__main__": main()
