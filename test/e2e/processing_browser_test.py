"""Sandboxed Chrome -> real C++/isolated MySQL; real parser, no model calls or mock API."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

import m2_worker_test as support
from m1_browser_test import Browser, dependencies
from m1_http_test import create_user, data, HttpClient, login
from smartdocs import worker


class ProcessingBrowser(support.WorkerE2E):
    def test_processing_controls_use_real_persisted_state(self):
        output = Path(os.environ["SMARTDOCS_PROCESSING_BROWSER_OUTPUT"])
        output.mkdir(parents=True, exist_ok=True)
        binaries, sandbox = dependencies(version_timeout=120)
        self.assertEqual(sandbox, "enabled")
        job = self.upload("handover.txt", b"Restore the backup before recovery.", "text/plain")
        claimed = self.client.claim()
        self.assertEqual(claimed["id"], job["processing_job_id"])
        self.client.fail(claimed, "worker_failed")
        reader = create_user(str(support.ROOT/"bin/smartdocs-admin"), "processing-reader", self.password)
        data(self.user.request("PUT", f'/api/v1/projects/{self.project["id"]}/members/{reader}', {"role": "reader"}))
        with tempfile.TemporaryDirectory(prefix="smartdocs-processing-browser-") as profile:
            browser = Browser(binaries, sandbox, profile, output/"driver.log")
            try:
                browser.start()
                browser.command("POST", "/url", {"url": f"http://127.0.0.1:{self.server.port}/app.html"})
                def sign_in(username):
                    browser.wait('return !document.querySelector("#login-form").hidden && document.querySelector("#global-status").textContent === "idle"')
                    browser.fill("#username", username)
                    browser.fill("#password", self.password)
                    browser.click('#login-form button[type="submit"]')
                    browser.wait('return !document.querySelector("#signed-in").hidden && document.querySelector("#current-user").textContent === arguments[0] && document.querySelector("#global-status").textContent === "idle"', username)
                    browser.wait('return document.querySelectorAll("#file-rows tr").length === 1')
                    browser.click("#file-rows button")
                    browser.wait('return document.querySelector("#processing-jobs").textContent.includes("Task ")')
                def state_contains(value):
                    browser.wait('return document.querySelector("#processing-jobs").textContent.includes(arguments[0])', value)
                sign_in("worker-e2e-admin")
                state_contains("worker_failed")
                browser.click("#processing-jobs button")
                state_contains("State: pending")
                self.assertEqual(self.state(job)["state"], "pending")
                browser.click("#processing-jobs button")
                state_contains("State: cancelled")
                self.assertEqual(self.state(job)["state"], "cancelled")
                browser.command("POST", "/refresh", {})
                browser.wait('return document.querySelectorAll("#file-rows tr").length === 1')
                browser.click("#file-rows button")
                state_contains("State: cancelled")
                self.assertIn(job["processing_job_id"], browser.inspect('return document.querySelector("#processing-jobs").textContent'))
                browser.click("#processing-jobs button")
                state_contains("State: pending")
                self.assertEqual(worker.run_once(self.client), "completed")
                browser.click("#processing-refresh")
                state_contains("State: completed")
                status = browser.inspect('return document.querySelector("#processing-status").textContent')
                self.assertIn("Parse: completed", status)
                self.assertIn("Keyword: completed", status)
                self.assertIn("Vector: not_applicable", status)

                browser.click('#document-search-mode option[value="keyword"]')
                browser.fill("#document-search-query", "backup")
                browser.click("#document-search-submit")
                browser.wait('return document.querySelectorAll(".search-card").length === 1')
                browser.click('#remote-ai-policy option[value="remote_ai_allowed"]')
                browser.click('#approved-'+job["version_id"])
                browser.click('#remote-ai-form button[type="submit"]')
                browser.wait('return document.querySelector("#detail-status").textContent.includes("policy saved")')
                self.assertEqual(browser.inspect('return document.querySelectorAll(".search-card").length'), 0)
                browser.click("#version-list button")
                browser.wait('return document.querySelectorAll("#processing-jobs .task-card").length === 2')
                vector_id = self.document(job)["vector_job_id"]
                self.jobs.append({**job, "processing_job_id": vector_id})
                browser.click("#processing-jobs button")
                state_contains("State: cancelled")
                browser.click("#processing-jobs button")
                state_contains("State: pending")
                self.assertEqual(self.document(job)["keyword_state"], "completed")
                for width in (360, 375, 390, 414, 768, 1280):
                    browser.command("POST", "/goog/cdp/execute", {"cmd": "Emulation.setDeviceMetricsOverride",
                        "params": {"width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": False}})
                    geometry = browser.inspect('''const p = document.querySelector('#processing-panel');
                        return {width: innerWidth, overflow: p.scrollWidth > p.clientWidth,
                            small: [...p.querySelectorAll('button')].some(n => n.getBoundingClientRect().height < 44)};''')
                    self.assertEqual(geometry, {"width": width, "overflow": False, "small": False})
                    if width in (360, 1280):
                        browser.inspect('document.querySelector("#processing-panel").scrollIntoView({block:"start"})')
                        raw = browser.command("GET", "/element/"+browser.find("#processing-panel")+"/screenshot")
                        (output/f"processing-{width}.png").write_bytes(base64.b64decode(raw, validate=True))

                browser.click("#logout-button")
                sign_in("processing-reader")
                self.assertEqual(browser.inspect('return document.querySelectorAll("#processing-jobs button").length'), 0)
                reader_http = HttpClient(self.server.port)
                login(reader_http, "processing-reader", self.password)
                self.assertEqual(reader_http.request("POST", self.job_path(job)+"/retry").status, 403)
                self.assertEqual(self.user.request("DELETE", f'/api/v1/projects/{self.project["id"]}/files/{job["file_id"]}').status, 204)
                try:
                    browser.click("#processing-refresh")
                    browser.wait('return document.querySelector("#processing-status").textContent.includes("resource_not_found")')
                    self.assertEqual(browser.inspect('return document.querySelector("#processing-jobs").children.length'), 0)
                finally:
                    # Restore even on assertion failure so task cleanup can query this synthetic file.
                    data(self.user.request("POST", f'/api/v1/projects/{self.project["id"]}/files/{job["file_id"]}/restore'))
                print("PASS real Chrome/C++/MySQL/parser: failed -> retry -> pending -> cancel -> reload -> retry -> completed; vector cancel/retry independent; reader denied; deletion refresh; approval clears search; six viewports", flush=True)
            finally:
                browser.stop()


if __name__ == "__main__":
    suite = unittest.TestSuite([ProcessingBrowser("test_processing_controls_use_real_persisted_state")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
