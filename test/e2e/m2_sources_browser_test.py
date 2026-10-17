"""Real parser -> C++/isolated MySQL -> Chrome source links; no model or API mocks."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest

from m1_browser_test import Browser, dependencies, png_details
from m1_http_test import HttpClient
from m2_worker_test import WorkerE2E, source_examples


class SourceBrowser(WorkerE2E):
    def test_three_formats_source_locations_and_links(self):
        output = Path(os.environ["SMARTDOCS_SOURCE_BROWSER_OUTPUT"])
        output.mkdir(parents=True, exist_ok=True)
        examples = []
        # Finish parser subprocesses before Chrome competes for the capped page cache.
        for name, content, mime, _, segments, query in source_examples():
            job = self.upload(name, content, mime)
            self.assertIn("worker: completed", self.cli().stdout)
            self.assertEqual(self.document(job)["chunk_count"], len(segments))
            examples.append((name, content, segments, query, job))
        binaries, sandbox = dependencies(version_timeout=120)
        self.assertEqual(sandbox, "enabled")
        origin = f"http://127.0.0.1:{self.server.port}"
        with tempfile.TemporaryDirectory(prefix="smartdocs-source-browser-") as profile:
            browser = Browser(binaries, sandbox, profile, output/"driver.log")
            try:
                browser.start()
                browser.command("POST", "/url", {"url": origin+"/app.html"})
                browser.wait('return document.querySelector("#global-status").textContent === "idle"')
                browser.fill("#username", "worker-e2e-admin")
                browser.fill("#password", self.password)
                browser.click('#login-form button[type="submit"]')
                browser.wait('return !document.querySelector("#signed-in").hidden && document.querySelector("#global-status").textContent === "idle"')

                def text(selector):
                    return browser.inspect('return document.querySelector(arguments[0]).textContent', selector)

                for name, content, segments, query, job in examples:
                    browser.click('#document-search-mode option[value="keyword"]')
                    browser.fill("#document-search-query", query)
                    browser.click("#document-search-submit")
                    browser.wait('return document.querySelector("#document-search-status").textContent.includes("results · keyword_only")')
                    cards = browser.inspect('return Array.from(document.querySelectorAll(".search-card"), c => c.textContent)')
                    # Natural-language ngram can match other files; scope exact counts by identity.
                    identity = f'File {job["file_id"]} · Version {job["version_id"]}'
                    target_cards = [i for i, card in enumerate(cards) if identity in card]
                    self.assertEqual(len(target_cards), len(segments), cards)
                    print(json.dumps({"search": name, "total_cards": len(cards),
                        "target_cards": len(target_cards)}), flush=True)
                    for expected, page, last_line in segments:
                        matches = [i for i in target_cards if expected in cards[i]]
                        self.assertEqual(len(matches), 1, cards)
                        browser.click(f'.search-card:nth-child({matches[0]+1}) button')
                        browser.wait('return document.querySelector("#document-search-source pre") !== null')
                        self.assertEqual(text("#document-search-source pre"), expected)
                        panel = text("#document-search-source")
                        self.assertIn(f'File {job["file_id"]} · Version {job["version_id"]}', panel)
                        self.assertIn(f"Lines 1–{last_line}", panel)
                        self.assertEqual("PDF page" in panel, bool(page))
                        if page:
                            self.assertIn(f"PDF page {page} ·", panel)
                        href = browser.inspect('return document.querySelector("#document-search-source a").href')
                        self.assertEqual(href, origin+self.version_path(job)+"/content"+(f"#page={page}" if page else ""))
                        self.assertIn("Current version", text("#document-source-status"))
                        # Follow the displayed URL's HTTP part with/without authorization.
                        path = href.removeprefix(origin).split("#", 1)[0]
                        response = self.user.request("GET", path)
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.body, content)
                        self.assertEqual(HttpClient(self.server.port).request("GET", path).status, 401)
                        if page == 2:
                            for width in (1280, 360):
                                browser.command("POST", "/goog/cdp/execute", {"cmd": "Emulation.setDeviceMetricsOverride",
                                    "params": {"width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": False}})
                                browser.inspect('document.querySelector("#document-search-source").scrollIntoView({block:"start"})')
                                raw = browser.command("GET", "/element/"+browser.find("#document-search-source")+"/screenshot")
                                png = base64.b64decode(raw, validate=True)
                                png_details(png)
                                (output/f"pdf-page2-source-{width}.png").write_bytes(png)
                        print(json.dumps({"browser_source": name, "page": page, "lines": [1, last_line],
                            "exact_version_link": True, "original_bytes_verified": True, "anonymous_refused": True}), flush=True)
            finally:
                browser.stop()


if __name__ == "__main__":
    suite = unittest.TestSuite([SourceBrowser("test_three_formats_source_locations_and_links")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
