"""Real browser -> test-only same-origin proxy -> gateway/C++/MySQL/offline model.

No fabricated API responses. This loopback proxy is test wiring, NOT deployment code.
Requires the existing capped environment and a separately network-isolated encoder.
"""
import base64
from contextlib import contextmanager
from functools import partial
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest

from m1_browser_test import Browser, dependencies, png_details
from m1_http_test import complete, create_upload, data, upload_parts
from m2_vector_worker_test import VectorE2E
from smartdocs import vector_worker, worker
from smartdocs.search_http import SearchHTTPServer


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def same_origin_site(backend_port, encoder):
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # Never log cookies, credentials or bodies.

        def relay(self):
            if self.path.startswith("/internal/"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64 * 1024:
                self.send_error(413)
                return
            port = gateway.server_port if re.fullmatch(
                r"/api/v1/projects/[0-9a-f]{32}/hybrid-search", self.path) else backend_port
            upstream = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
            try:
                headers = {name: self.headers[name] for name in
                    ("Host", "Origin", "Cookie", "Content-Type") if name in self.headers}
                headers["Connection"] = "close"
                upstream.request(self.command, self.path, self.rfile.read(length) if length else None, headers)
                response = upstream.getresponse()
                body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    raise ValueError("test response limit exceeded")
                self.send_response_only(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in ("connection", "content-length", "transfer-encoding"):
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
            finally:
                upstream.close()

        do_GET = do_POST = relay

    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as proxy:
        origin = f"http://127.0.0.1:{proxy.server_port}"
        with SearchHTTPServer(0, backend_port, origin, encoder) as gateway:
            with running(gateway), running(proxy):
                yield origin, gateway


class HybridBrowser(VectorE2E):
    def test_browser_hybrid_sources_version_change_and_failure(self):
        output = Path(os.environ["SMARTDOCS_HYBRID_BROWSER_OUTPUT"])
        output.mkdir(parents=True, exist_ok=True)
        binaries, sandbox = dependencies(version_timeout=120)
        self.assertEqual(sandbox, "enabled")
        original = b"Database disaster recovery: restore the latest backup to recover lost data."
        recovery = self.upload("recovery.txt", original, "text/plain")
        internal = self.upload("internal.txt", b"Internal backup material must not enter hybrid results.", "text/plain")
        with same_origin_site(self.server.port, self.encoder) as (origin, gateway), \
                tempfile.TemporaryDirectory(prefix="smartdocs-hybrid-browser-") as profile:
            browser = Browser(binaries, sandbox, profile, output/"driver.log")
            try:
                # Start Chrome before model loading competes for the capped page cache.
                browser.start()
                for job in (recovery, internal):
                    self.assertEqual(worker.run_once(self.client), "completed")
                self.policy(recovery, True)
                self.jobs.append(self.vector_job(recovery))
                result = vector_worker.run_once(self.vector, self.encoder)
                if result == "failed":
                    vector = self.vector_job(recovery)
                    state = self.state(vector)
                    self.assertEqual(state["state"], "failed")
                    self.assertEqual(state["error_code"], "embedding_timeout")
                    self.assertEqual(self.document(recovery)["vector_state"], "failed")
                    print("OBSERVED cold encoding timeout: persisted failure; exercising one explicit API retry", flush=True)
                    data(self.user.request("POST", self.job_path(vector)+"/retry"))
                    self.assertEqual(self.state(vector)["state"], "pending")
                    result = vector_worker.run_once(self.vector, self.encoder)
                self.assertEqual(result, "completed", self.state(self.vector_job(recovery)).get("error_code"))
                self.assertEqual(self.document(recovery)["vector_state"], "completed")
                private = self.document(internal)
                self.assertEqual(private["vector_state"], "not_applicable")
                self.assertIsNone(private["vector_job_id"])
                print("PASS fixture: real approved vectors published; internal document not vectorized", flush=True)
                browser.command("POST", "/url", {"url": origin+"/app.html"})
                browser.wait('return document.querySelector("#global-status").textContent === "idle"')
                browser.fill("#username", "worker-e2e-admin")
                browser.fill("#password", self.password)
                browser.click('#login-form button[type="submit"]')
                browser.wait('return !document.querySelector("#signed-in").hidden && document.querySelector("#global-status").textContent === "idle"')

                def text(selector):
                    return browser.inspect('return document.querySelector(arguments[0]).textContent', selector)

                def search(mode, query, count, retrieval):
                    browser.click(f'#document-search-mode option[value="{mode}"]')
                    browser.fill("#document-search-query", query)
                    browser.click("#document-search-submit")
                    # Production's bounded encoder can take up to a minute on capped cold I/O.
                    deadline = time.monotonic()+110
                    while text("#document-search-status").startswith("Searching") and time.monotonic() < deadline:
                        time.sleep(.2)
                    status = text("#document-search-status")
                    self.assertIn(f"{count} results", status, status)
                    self.assertIn(retrieval, status, status)
                    self.assertEqual(browser.inspect('return document.querySelectorAll(".search-card").length'), count)
                    return status

                search("hybrid", "如何恢复备份？", 1, "hybrid")
                self.assertIn("Matched by: vector", text("#document-search-results"))
                self.assertNotIn("internal.txt", text("#document-search-results"))
                browser.click(".search-card button")
                browser.wait('return document.querySelector("#document-search-source pre") !== null')
                self.assertEqual(text("#document-search-source pre"), original.decode())
                self.assertIn(recovery["version_id"], text("#document-search-source"))
                self.assertIn("Lines 1–1", text("#document-search-source"))
                self.assertEqual(browser.inspect('return document.querySelector("#document-search-source a").href'),
                    origin+f'/api/v1/projects/{self.project["id"]}/files/{recovery["file_id"]}/versions/{recovery["version_id"]}/content')
                for width in (360, 1280):
                    browser.command("POST", "/goog/cdp/execute", {"cmd": "Emulation.setDeviceMetricsOverride",
                        "params": {"width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": False}})
                    browser.inspect('document.querySelector("#document-search").scrollIntoView({block:"start"})')
                    raw = browser.command("GET", "/element/"+browser.find("#document-search")+"/screenshot")
                    png = base64.b64decode(raw, validate=True)
                    png_details(png)
                    (output/f"hybrid-real-{width}.png").write_bytes(png)
                print("PASS browser: real semantic-only Chinese query -> English source, exact version and lines", flush=True)

                gateway.encoder = partial(vector_worker.request_encoding, str(self.root/"unavailable.sock"))
                status = search("hybrid", "backup", 1, "keyword_only")
                self.assertIn("query_embedding_unavailable", status)
                self.assertIn("degraded to keywords within the same approved scope", status)
                self.assertNotIn("internal.txt", text("#document-search-results"))

                replacement = b"Replacement release notes without the prior recovery instructions."
                task = create_upload(self.user, self.project["id"], self.project["root_directory_id"],
                    "recovery.txt", replacement, "text/plain", recovery["file_id"], recovery["version_id"])
                upload_parts(self.user, self.project["id"], task, replacement)
                latest = data(complete(self.user, self.project["id"], task["task_id"]))
                self.jobs.append(latest)
                browser.click(".search-card button")
                browser.wait('return document.querySelector("#document-source-status").textContent.includes("historical version")')
                self.assertEqual(text("#document-search-source pre"), original.decode())
                self.assertIn(recovery["version_id"], text("#document-search-source"))
                search("hybrid", "backup", 0, "keyword_only")
                search("metadata", "recovery.txt", 1, "metadata")
                self.assertIn(latest["version_id"], text("#document-search-results"))
                self.assertIn("Parse: pending", text("#document-search-results"))
                self.assertEqual(worker.run_once(self.client), "completed")
                search("hybrid", "Replacement", 0, "keyword_only")
                search("keyword", "Replacement", 1, "keyword_only")
                self.assertNotIn(recovery["version_id"], text("#document-search-results"))
                self.policy(latest, True)
                self.jobs.append(self.vector_job(latest))
                search("hybrid", "Replacement", 1, "keyword_only")
                self.policy(latest, False)
                search("hybrid", "Replacement", 0, "keyword_only")
                print("PASS browser: explicit encoder failure, no internal leak, immutable historical source, new-version exclusion/discovery, approval not inherited, revocation", flush=True)
            finally:
                browser.stop()


if __name__ == "__main__":
    suite = unittest.TestSuite([HybridBrowser("test_browser_hybrid_sources_version_change_and_failure")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
