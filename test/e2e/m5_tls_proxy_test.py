"""Isolated real TLS/C++ check; gateway receivers verify proxy routing only."""
import argparse
from contextlib import ExitStack
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import threading
import time

from m1_http_test import (HttpClient, Response, ServerProcess, create_user, data,
                         upload_file)

ROOT = Path(__file__).resolve().parents[2]


class TLSClient(HttpClient):
    def __init__(self, port, certificate):
        super().__init__(port)
        self.context = ssl.create_default_context(cafile=str(certificate))

    def request(self, method, path, body=None, headers=None):
        headers = dict(headers or {})
        headers.setdefault("Host", f"127.0.0.1:{self.port}")
        if method in {"POST", "PUT", "DELETE", "PATCH"}:
            headers.setdefault("Origin", f"https://127.0.0.1:{self.port}")
        if self._cookie_header():
            headers["Cookie"] = self._cookie_header()
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPSConnection(
            "127.0.0.1", self.port, context=self.context, timeout=10)
        try:
            connection.request(method, path, body, headers)
            raw = connection.getresponse()
            pairs = raw.getheaders()
            result = Response(raw.status, {k.lower(): v for k, v in pairs}, raw.read())
            self._save_cookies([v for k, v in pairs if k.lower() == "set-cookie"])
            return result
        finally:
            connection.close()


def snippet(relative):
    blocks = re.findall(r"```nginx\n(.*?)```", (ROOT / relative).read_text(), re.S)
    if len(blocks) != 1:
        raise RuntimeError("expected one nginx template: " + relative)
    return blocks[0]


def configuration(directory, port, backend, search, agent):
    """Compose existing templates; substitute only isolated addresses and TLS paths."""
    site = snippet("docs/deployment/m1-single-host.md")
    site = site.replace("listen 443 ssl http2;", f"listen 127.0.0.1:{port} ssl http2;")
    site = site.replace("server_name docs.example.com;", "server_name 127.0.0.1;")
    site = site.replace("127.0.0.1:1316", f"127.0.0.1:{backend}")
    routes, zones = [], []
    for source, old, new in (
        ("docs/deployment/m2-hybrid-search.md", 1317, search),
        ("docs/api/m3-agent-http.md", 1318, agent),
    ):
        lines = snippet(source).splitlines()
        zones.extend(line for line in lines if line.startswith(("limit_req_zone ", "limit_conn_zone ")))
        block = "\n".join(line for line in lines if not line.startswith(
            ("limit_req_zone ", "limit_conn_zone ", "client_header_timeout ")))
        routes.append(block.replace(f"127.0.0.1:{old}", f"127.0.0.1:{new}"))
    routes.append(snippet("docs/deployment/m2-local-worker.md"))
    site = site.rstrip().removesuffix("}") + "\nclient_header_timeout 5s;\n" + "\n".join(routes) + "\n}"
    return f"""daemon off;
worker_processes 1;
pid {directory}/nginx.pid;
error_log {directory}/error.log warn;
events {{ worker_connections 64; }}
http {{
    access_log off;
    client_body_temp_path {directory}/body;
    proxy_temp_path {directory}/proxy;
    fastcgi_temp_path {directory}/fastcgi;
    uwsgi_temp_path {directory}/uwsgi;
    scgi_temp_path {directory}/scgi;
    ssl_certificate {directory}/cert.pem;
    ssl_certificate_key {directory}/key.pem;
    server {{
        listen 127.0.0.1:{port} ssl default_server;
        server_name _;
        return 444;
    }}
    {chr(10).join(zones)}
    {site}
}}
"""


def receiver(stack):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append((self.path, self.headers.get("Host"), self.headers.get("Origin"), body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    def close():
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()

    stack.callback(close)
    return server.server_port, calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0 or not re.fullmatch(
            r"/tmp/smartdocs-mysql\.[^/]+/mysql.sock", os.environ.get("SMARTDOCS_MYSQL_SOCKET", "")):
        raise RuntimeError("non-root isolated MySQL wrapper required")
    with tempfile.TemporaryDirectory(prefix="smartdocs-tls.") as temporary, ExitStack() as stack:
        directory = Path(temporary)
        for name in ("storage", "storage/objects", "storage/staging"):
            (directory / name).mkdir(mode=0o700)
        os.environ.update(SMARTDOCS_STORAGE_ROOT=str(directory / "storage"),
                          SMARTDOCS_PASSWORD_ITERATIONS="1000", SMARTDOCS_THREAD_COUNT="2",
                          SMARTDOCS_MYSQL_POOL_SIZE="4", SMARTDOCS_MAX_FILE_BYTES="1048576",
                          SMARTDOCS_CHUNK_BYTES="65536", SMARTDOCS_MAX_JSON_BYTES="65536",
                          SMARTDOCS_LISTEN_ADDRESS="127.0.0.1",
                          SMARTDOCS_SECURE_COOKIE="true", SMARTDOCS_LOG_LEVEL="0",
                          SMARTDOCS_WORKER_TOKEN="b" * 64)
        create_user(str(ROOT / "bin/smartdocs-admin"), "tls-fixture", "tls-test-only-password")
        server = ServerProcess(ROOT / "bin/server", directory / "server-logs")
        stack.callback(server.stop)
        server.start()
        search_port, search_calls = receiver(stack)
        agent_port, agent_calls = receiver(stack)
        port = ServerProcess._free_port()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", str(directory / "key.pem"), "-out", str(directory / "cert.pem")],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        config = directory / "nginx.conf"
        config.write_text(configuration(directory, port, server.port, search_port, agent_port))
        command = [str(args.nginx.resolve()), "-p", str(directory), "-c", str(config)]
        subprocess.run([*command, "-t"], check=True, capture_output=True, timeout=15)
        print("PASS nginx_config_test", flush=True)
        logs = stack.enter_context((directory / "nginx-process.log").open("wb"))
        process = subprocess.Popen(command, stdout=logs, stderr=subprocess.STDOUT)

        def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)

        stack.callback(stop)
        client = TLSClient(port, directory / "cert.pem")
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
        assert client.request("GET", "/api/v1/me").status == 401
        print("PASS verified_tls_ready_and_anonymous_denial", flush=True)
        denied = client.request("POST", "/api/v1/auth/login",
                                {"username": "tls-fixture", "password": "tls-test-only-password"},
                                {"Origin": "https://wrong.invalid"})
        assert denied.status == 403
        response = client.request("POST", "/api/v1/auth/login",
                                  {"username": "tls-fixture", "password": "tls-test-only-password"})
        assert response.status == 200
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        assert client.request("GET", "/api/v1/me").status == 200
        print("PASS real_cpp_origin_and_secure_session", flush=True)
        project = data(client.request("POST", "/api/v1/projects", {"name": "TLS synthetic"}), 201)
        content = b"TLS fixed version fixture\n" * 3000
        _, uploaded = upload_file(client, project["id"], project["root_directory_id"],
                                  "tls.txt", content, "text/plain")
        base = f'/api/v1/projects/{project["id"]}'
        url = f'{base}/files/{uploaded["file_id"]}/versions/{uploaded["version_id"]}/content'
        assert client.request("GET", url).body == content
        ranged = client.request("GET", url, headers={"Range": "bytes=7-31"})
        assert ranged.status == 206 and ranged.body == content[7:32]
        assert ranged.headers["content-range"] == f"bytes 7-31/{len(content)}"
        assert TLSClient(port, directory / "cert.pem").request("GET", url).status == 401
        print("PASS real_cpp_chunk_upload_download_range", flush=True)
        # A real enabled internal endpoint returns 401 directly, but must be hidden by nginx.
        internal = "/internal/v1/processing/recover"
        assert HttpClient(server.port).request("POST", internal).status == 401
        assert client.request("POST", internal).status == 404
        assert client.request("GET", "/internal/anything").status == 404
        try:
            client.request("GET", "/api/v1/health/ready", headers={"Host": "wrong.invalid"})
        except http.client.RemoteDisconnected:
            pass
        else:
            raise AssertionError("unknown Host was not rejected")
        print("PASS_internal_and_unknown_host_denied", flush=True)
        origin = f"https://127.0.0.1:{port}"
        task = "a" * 32
        routes = ((f"{base}/hybrid-search", search_calls),
                  (f"{base}/agent-tasks/{task}/execute", agent_calls))
        for path, calls in routes:
            assert client.request("POST", path, {}).status == 204
            assert calls == [(path, f"127.0.0.1:{port}", origin, b"{}")]
            assert client.request("POST", path, b"x" * 4097).status == 413
            assert len(calls) == 1
        # State/cancel are C++ routes, not the execution gateway.
        assert client.request("GET", f"{base}/agent-tasks/{task}").status == 404
        invalid_cancel = client.request("POST", f"{base}/agent-tasks/{task}/cancel", {})
        assert invalid_cancel.status == 400
        assert invalid_cancel.json()["error"]["code"] == "body_not_allowed"
        assert client.request("POST", f"{base}/agent-tasks/{task}/cancel").status == 404
        assert len(search_calls) == len(agent_calls) == 1
        print("PASS_exact_gateway_routes_headers_body_limit_and_cpp_state", flush=True)
        print(json.dumps({"result": "pass", "checks": 6, "remote_model_calls": 0,
                          "gateway_business": "not_run", "public_tls": "not_run",
                          "uploaded_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}),
              flush=True)


if __name__ == "__main__":
    main()
