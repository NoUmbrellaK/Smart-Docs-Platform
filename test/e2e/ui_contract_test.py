#!/usr/bin/env python3
"""Static browser-contract checks for the build-free M1 workbench."""

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "resources"


class WorkbenchParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.labels = set()
        self.controls = []
        self.sections = []
        self.scripts = []
        self.stylesheets = []
        self.live_regions = []
        self.links = []
        self.viewport = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "label" and "for" in values:
            self.labels.add(values["for"])
        if tag in {"input", "select", "textarea"}:
            self.controls.append(values)
        if tag == "section":
            self.sections.append(values)
        if tag == "script" and "src" in values:
            self.scripts.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href"))
        if tag == "a":
            self.links.append(values)
        if values.get("aria-live") in {"polite", "assertive"} or \
                values.get("role") in {"status", "alert"}:
            self.live_regions.append(values)
        if tag == "meta" and values.get("name") == "viewport":
            self.viewport = values.get("content") == \
                "width=device-width, initial-scale=1.0"


def read_resource(relative_path):
    path = RESOURCES / relative_path
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class UiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read_resource("app.html")
        cls.parser = WorkbenchParser()
        cls.parser.feed(cls.html)
        cls.css = read_resource("css/app.css")
        cls.api = read_resource("js/api.js")
        cls.app = read_resource("js/app.js")
        cls.uploads = read_resource("js/uploads.js")
        cls.sha256 = read_resource("js/sha256.js")
        cls.index_parser = WorkbenchParser()
        cls.index_parser.feed(read_resource("index.html"))

    def test_required_assets_exist(self):
        for relative_path in ("app.html", "css/app.css", "js/api.js",
                              "js/app.js", "js/uploads.js", "js/sha256.js"):
            self.assertTrue((RESOURCES / relative_path).is_file(),
                            f"required UI asset is missing: {relative_path}")

    def test_semantic_page_shell_is_accessible(self):
        expected_sections = {"session", "workbench", "upload-tasks",
                             "file-detail", "project-settings"}
        actual_sections = {section.get("id") for section in self.parser.sections}
        self.assertEqual(expected_sections, actual_sections)
        self.assertTrue(self.parser.viewport)
        self.assertGreaterEqual(len(self.parser.live_regions), 2)
        for control in self.parser.controls:
            control_id = control.get("id")
            self.assertTrue(control.get("aria-label") or
                            (control_id and control_id in self.parser.labels),
                            f"unlabelled form control: {control}")
        self.assertIn("/css/app.css", self.parser.stylesheets)
        self.assertIn("/js/app.js", self.parser.scripts)
        self.assertTrue(any(link.get("href") == "/app.html"
                            for link in self.index_parser.links))

    def test_modules_use_safe_same_origin_api_contract(self):
        combined = "\n".join((self.api, self.app, self.uploads))
        self.assertNotRegex(combined, r"https?://")
        self.assertNotRegex(combined, r"\.innerHTML\s*=")
        self.assertIn('const API_ROOT = "/api/v1"', self.api)
        self.assertIn('credentials: "same-origin"', self.api)
        self.assertIn("class ApiError", self.api)
        for field in ("status", "code", "retryability", "requestId"):
            self.assertIn(field, self.api)
        for safe_render_api in ("textContent", "createElement", "replaceChildren"):
            self.assertIn(safe_render_api, self.app)

    def test_app_connects_all_m1_business_surfaces(self):
        for endpoint_fragment in (
                "/auth/login", "/auth/logout", "/me", "/projects/",
                "/directories", "/files", "/versions", "/content",
                "/restore", "/remote-ai-policy", "/members"):
            self.assertIn(endpoint_fragment, self.app + self.api)
        for behavior in ("deleted", "name", "directory_id", "#page=",
                         "approved_version_ids", "admin", "reader", "editor"):
            self.assertIn(behavior, self.app)
        for state in ("idle", "loading", "awaiting reselect", "uploading",
                      "assembling", "interrupted", "failed", "cancelled",
                      "completed"):
            self.assertIn(state, self.app + self.uploads + self.html)
        self.assertIn("CANCELLABLE_UPLOAD_STATES", self.app)

    def test_upload_controller_is_resumable_and_bounded(self):
        self.assertRegex(self.uploads, r"MAX_PARALLEL_PARTS\s*=\s*4")
        for contract in ("create_file", "create_version", "confirmed_parts",
                         "chunk_size", "X-Chunk-SHA256",
                         "application/octet-stream", ".slice(", "received_bytes",
                         "file_id", "version_id", "processing_job_id"):
            self.assertIn(contract, self.uploads)
        self.assertIn("awaiting reselect", self.uploads)
        self.assertIn("file.size", self.uploads)
        self.assertIn("sha256", self.uploads)
        self.assertIn("projectId: detail.project_id", self.uploads)
        self.assertIn("for (const runtime of this.runtimes.values())", self.uploads)
        self.assertRegex(self.uploads, r"complete(?:Promise|Started|Once|Guard)")
        self.assertNotIn("crypto.subtle.digest", self.uploads + self.sha256)

    def test_sha256_is_pinned_official_incremental_module(self):
        self.assertIn("emn178/js-sha256", self.sha256)
        self.assertIn("v0.11.1", self.sha256)
        self.assertIn("MIT", self.sha256)
        self.assertIn("export const sha256", self.sha256)
        self.assertEqual(1, len(re.findall(r"\bexport\s+", self.sha256)))
        for method in ("create", "update", "hex"):
            self.assertIn(method, self.sha256)

    def test_css_is_mobile_first_and_touch_safe(self):
        self.assertIn("box-sizing: border-box", self.css)
        self.assertRegex(self.css, r"min-height:\s*44px")
        self.assertRegex(self.css, r"font-size:\s*(?:1rem|16px)")
        self.assertRegex(self.css, r"@media\s*\(min-width:")
        self.assertNotRegex(self.css, r"@media\s*\(max-width:")
        self.assertRegex(self.css, r"overflow-x:\s*(?:auto|hidden|clip)")


if __name__ == "__main__":
    unittest.main()
