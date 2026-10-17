#!/usr/bin/env python3
"""Real Chrome workbench smoke with an explicit local HTTP API fixture, not C++ E2E.

Run non-root in the documented resource-capped unit. Reuses the M1 browser driver.
No database, credentials, model calls or third-party Python packages.
"""
import base64
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import struct
import tempfile
import threading

from m1_browser_test import Browser, dependencies


ROOT = Path(__file__).resolve().parents[2]
ITEM = {"file_id": "f" * 32, "version_id": "v" * 32,
        "name": "<img src=x onerror=alert(1)>" + "long-name-" * 12,
        "directory_id": "d" * 32, "media_type": "application/pdf",
        "parse_state": "completed", "keyword_state": "completed",
        "vector_state": "completed", "availability_state": "available"}
CHUNK = {"chunk_id": "c" * 64, "text": "<script>not executable</script>\nHandover source.",
         "page": 2, "line_start": 1, "line_end": 2}


class Fixture(SimpleHTTPRequestHandler):
    requests = []
    deny_source = False
    degraded = False
    mutation_started = threading.Event()
    mutation_release = threading.Event()

    def log_message(self, *_):
        pass

    def reply(self, data, status=200):
        body = json.dumps({"data": data} if status == 200 else {
            "error": {"code": "forbidden", "message": "Access revoked"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.path.startswith("/api/"):
            return super().do_GET()
        if self.path == "/api/v1/me":
            return self.reply({"user": {"id": "u", "username": "UI fixture reader"},
                               "projects": [{"id": "p", "name": "Fixture project", "role": "editor"}]})
        if "/files?" in self.path:
            return self.reply({"items": [{"id": name, "name": name, "directory_id": "d",
                "remote_ai_policy": "internal_only", "deleted": False, "current_version_id": "a" * 32}
                for name in ("first", "second")]})
        if "/uploads?" in self.path:
            return self.reply({"items": [{"task_id": "resume", "state": "uploading"}], "total": 1})
        if self.path.endswith("/uploads/resume"):
            return self.reply({"task_id": "resume", "project_id": "p", "mode": "create_version",
                "name": "empty.txt", "file_id": "first", "size": 0, "chunk_size": 16, "part_count": 0,
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "media_type": "text/plain", "confirmed_parts": [], "state": "uploading"})
        if "/chunks/" in self.path or self.path.endswith("/document"):
            Fixture.requests.append(self.path)
            if self.deny_source:
                return self.reply({}, 403)
            source = {**ITEM, "is_current": False, "pages_without_text": [1]}
            if "/chunks/" in self.path:
                source["chunk"] = CHUNK
            return self.reply(source)
        return self.reply({"items": [], "total": 0})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path.endswith("/uploads/resume/complete"):
            return self.reply({"file_id": "first", "version_id": "new", "processing_job_id": "job"})
        hybrid = self.path.endswith("/hybrid-search")
        if not self.path.endswith("/search") and not hybrid:
            return self.reply({}, 404)
        if hybrid:
            assert "mode" not in body, "gateway body must not contain a client mode"
        Fixture.requests.append(body)
        mode = "hybrid" if hybrid else body["mode"]
        item = dict(ITEM)
        if mode != "metadata":
            item["chunk"] = CHUNK
        if hybrid:
            item["match_sources"] = ["keyword" if self.degraded else "vector"]
        self.reply({"items": [item], "page": body["page"], "page_size": 10,
                    "next_page": 2 if body["page"] == 1 else None,
                    "has_more": body["page"] == 1, "truncated": body["page"] == 1,
                    "retrieval": "hybrid" if hybrid and not self.degraded else "metadata" if mode == "metadata" else "keyword_only",
                    "coverage": {"current_files": 3, "keyword_ready_files": 1, "unsearched_files": 2,
                                 **({"vector_completed_files": 1} if hybrid else {})},
                    "warnings": ["live_pagination"] + (["vector_search_unavailable"] if not hybrid or self.degraded else ["vector_index_partial"])})

    def do_DELETE(self):
        self.mutation_started.set()
        if not self.mutation_release.wait(30):
            return self.reply({}, 500)
        self.reply({})


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    # Cold executable loading under the ECS I/O cap can exceed 10 seconds.
    binaries, sandbox = dependencies(version_timeout=120)
    assert sandbox == "enabled", "This test requires non-root sandboxed Chrome"
    with tempfile.TemporaryDirectory(prefix="smartdocs-search-browser-") as profile:
        server = ThreadingHTTPServer(("127.0.0.1", 0),
            functools.partial(Fixture, directory=str(ROOT / "resources")))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = Browser(binaries, sandbox, profile, output / "fixture-driver.log")
        try:
            browser.start()
            browser.command("POST", "/url", {"url": f"http://127.0.0.1:{server.server_port}/app.html"})
            browser.wait('return !document.querySelector("#document-search-submit").disabled')
            browser.click("#document-search-submit")
            browser.wait('return document.querySelectorAll(".search-card").length === 1')
            assert "metadata" in browser.inspect('return document.querySelector("#document-search-status").textContent')
            browser.click('#document-search-mode option[value="keyword"]')
            browser.fill("#document-search-query", "handover")
            browser.click("#document-search-submit")
            browser.wait('return document.querySelector("#document-search-status").textContent.includes("keyword_only")')
            assert Fixture.requests[-1]["mode"] == "keyword"
            browser.click("#document-search-next")
            browser.wait('return document.querySelector("#document-search-status").textContent.includes("Page 2")')
            assert Fixture.requests[-1]["page"] == 2
            browser.click('#document-search-mode option[value="hybrid"]')
            browser.click("#document-search-submit")
            browser.wait('return document.querySelector("#document-search-status").textContent.includes("results · hybrid")')
            assert "mode" not in Fixture.requests[-1]
            assert "Approved current versions only" in browser.inspect('return document.querySelector("#document-search-status").textContent')
            assert "Matched by: vector" in browser.inspect('return document.querySelector(".search-card").textContent')
            browser.click(".search-card button")
            browser.wait('return document.querySelector("#document-source-status").textContent.includes("historical")')
            assert Fixture.requests[-1].endswith("/versions/" + ITEM["version_id"] + "/chunks/" + CHUNK["chunk_id"])
            assert browser.inspect('return document.querySelector("#document-search-source a").getAttribute("href")').endswith("/content#page=2")
            assert browser.inspect('return document.querySelectorAll("#document-search img, #document-search script").length') == 0
            for width in (360, 375, 390, 414, 768, 1280):
                browser.command("POST", "/goog/cdp/execute", {"cmd": "Emulation.setDeviceMetricsOverride",
                    "params": {"width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": False}})
                geometry = browser.inspect('''const panel = document.querySelector('#document-search');
                    return {width: innerWidth, overflow: panel.scrollWidth > panel.clientWidth,
                        small: [...panel.querySelectorAll('input,select,button,a')].some(n =>
                            n.getBoundingClientRect().height < 44)};''')
                assert geometry == {"width": width, "overflow": False, "small": False}, geometry
                if width in (360, 1280):
                    for suffix, selector in (("", "#document-search"), ("-result", ".search-card"),
                                             ("-source", "#document-search-source")):
                        browser.inspect('document.querySelector(arguments[0]).scrollIntoView({block: "start"})', selector)
                        element = browser.find(selector)
                        screenshot = browser.command("GET", "/element/" + element + "/screenshot")
                        png = base64.b64decode(screenshot, validate=True)
                        image_width, image_height = struct.unpack("!II", png[16:24])
                        assert image_width >= 200 and image_height >= 100, "Screenshot was clipped"
                        (output / f"search-{width}{suffix}.png").write_bytes(png)
            Fixture.degraded = True
            browser.click("#document-search-submit")
            browser.wait('return document.querySelector("#document-search-status").textContent.includes("degraded to keywords")')
            Fixture.deny_source = True
            browser.click(".search-card button")
            browser.wait('return document.querySelector("#document-source-status").textContent.includes("Access revoked")')
            assert browser.inspect('return document.querySelector("#document-search-source").children.length') == 0
            browser.fill("#document-search-query", "changed")
            assert browser.inspect('return document.querySelectorAll(".search-card").length') == 0
            browser.click("#document-search-submit")
            browser.wait('return document.querySelectorAll(".search-card").length === 1')
            browser.click("#file-rows tr:first-child button")
            browser.wait('return !document.querySelector("#delete-file").disabled')
            browser.click("#delete-file")
            assert Fixture.mutation_started.wait(10)
            browser.click("#file-rows tr:last-child button")
            browser.wait('return document.querySelector("#file-name").value === "second"')
            Fixture.mutation_release.set()
            browser.wait('return document.querySelectorAll(".search-card").length === 0')
            browser.click("#document-search-submit")
            browser.wait('return document.querySelectorAll(".search-card").length === 1')
            empty = Path(profile) / "empty.txt"
            empty.touch()
            browser.fill(".task-actions input", str(empty), file=True)
            browser.click(".task-actions button")
            browser.wait('return document.querySelector("#upload-list").textContent.includes("completed")')
            assert browser.inspect('return document.querySelectorAll(".search-card").length') == 0
            print("PASS: real Chrome + local HTTP fixture; metadata/keyword/hybrid, gateway route/body, approved scope, vector lane, degradation, pagination, fixed source, access error, safe text, filter reset, switched-selection delete, resumed upload; 6 viewport widths", flush=True)
        finally:
            browser.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
