"""Real Chrome/C++/isolated MySQL task UI. Answers are seeded protocol fixtures, not model output."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

from m1_browser_test import Browser, dependencies
from m1_http_test import HttpClient, create_user, data, login
from m2_worker_test import ROOT
from m3_task_list_test import TaskListE2E
from smartdocs import worker


class AgentBrowser(TaskListE2E):
    def test_browser_task_lifecycle_and_fixed_citations(self):
        output = Path(os.environ["SMARTDOCS_AGENT_BROWSER_OUTPUT"])
        output.mkdir(parents=True, exist_ok=True)
        binaries, sandbox = dependencies(version_timeout=120)
        self.assertEqual(sandbox, "enabled")
        asset = self.user.request("GET", "/js/agent.js")
        self.assertEqual(asset.status, 200)
        self.assertIn("text/javascript", asset.headers["content-type"])
        job = self.approved_file()
        self.assertEqual(worker.run_once(self.client), "completed")
        reader_id = create_user(str(ROOT/"bin/smartdocs-admin"), "agent-browser-reader", self.password)
        data(self.user.request("PUT", f'/api/v1/projects/{self.project["id"]}/members/{reader_id}', {"role": "reader"}))
        with tempfile.TemporaryDirectory(prefix="smartdocs-agent-browser-") as profile:
            browser = Browser(binaries, sandbox, profile)  # No potentially credential-bearing driver log.
            try:
                browser.start()
                browser.command("POST", "/url", {"url": f"http://127.0.0.1:{self.server.port}/app.html"})

                def sign_in(username):
                    browser.wait('return !document.querySelector("#login-form").hidden && document.querySelector("#global-status").textContent === "idle"')
                    browser.fill("#username", username)
                    browser.fill("#password", self.password)
                    browser.click('#login-form button[type="submit"]')
                    browser.wait('return document.querySelector("#global-status").textContent === "idle" && !document.querySelector("#agent-workspace").hidden')
                    browser.wait('return document.querySelector("#agent-list-status").textContent.includes("tasks on this page")')

                def create():
                    browser.click("#file-rows button")
                    browser.wait('return [...document.querySelectorAll("#version-list button")].some(b => b.textContent === "Add current version to Agent task")')
                    browser.click('//div[@id="version-list"]//button[text()="Add current version to Agent task"]', using="xpath")
                    browser.wait('return document.querySelector("#agent-scope").children.length === 1')
                    browser.fill("#agent-question", "Check public sample only")
                    self.assertFalse(browser.inspect('return document.querySelector("#agent-consent").checked'))
                    browser.click("#agent-consent")
                    browser.click("#agent-create")
                    browser.wait('return document.querySelector("#agent-create-status").textContent.includes("Created task")')
                    self.assertIn("waiting for an executor", browser.inspect('return document.querySelector("#agent-create-status").textContent'))
                    browser.wait('return document.querySelectorAll("#agent-list button").length > 0')
                    return data(self.user.request("GET", self.tasks_path()))["items"][0]

                sign_in("worker-e2e-admin")
                first = create()
                self.assertEqual(first["state"], "pending")
                browser.click("#agent-list button")
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("Waiting for an executor")')
                browser.click('//div[@id="agent-detail"]//button[text()="Load / refresh tool calls"]', using="xpath")
                browser.wait('return document.querySelector("#agent-audit-status").textContent.includes("No recorded backend requests")')
                browser.click('//div[@id="agent-detail"]//button[text()="Cancel task"]', using="xpath")
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("Cancelled")')
                self.assertEqual(data(self.user.request("GET", self.tasks_path()+"/"+first["id"]))["state"], "cancelled")
                browser.command("POST", "/refresh", {})
                browser.wait('return document.querySelector("#agent-list").textContent.includes("cancelled")')
                second = create()
                # Seed a validated persisted answer through the real protocol. No model was called.
                run = self.tasks_path()+"/"+second["id"]+"/run"
                data(self.user.request("POST", run+"/start"))
                data(self.user.request("POST", run+"/check"))
                tools = self.tasks_path()+"/"+second["id"]+"/tools"
                hits = data(self.user.request("POST", tools+"/search",
                                             {"mode": "keyword", "query": "SOURCE"}))
                chunk = hits["items"][0]["chunk"]
                data(self.user.request("POST", tools+"/read", {"file_id":job["file_id"],
                    "version_id":job["version_id"], "chunk_id":chunk["chunk_id"]}))
                self.assertEqual(self.user.request("POST", tools+"/read", {"secret":"DO-NOT-LOG"}).status,400)
                answer = {"answer": "Scripted UI fixture <img onerror=bad> — not a real model answer.",
                          "citations": [{"file_id": job["file_id"], "version_id": job["version_id"], "chunk_id": chunk["chunk_id"]}]}
                data(self.user.request("POST", run+"/finish", {"status": "completed", "result": answer, "error_code": None}))
                browser.click("#agent-refresh")
                browser.wait('return document.querySelector("#agent-list").textContent.includes("completed")')
                browser.click("#agent-list button")
                browser.wait('return document.querySelector("#agent-detail").textContent.includes("Scripted UI fixture")')
                self.assertEqual(browser.inspect('return document.querySelectorAll("#agent-detail img, #agent-detail script").length'), 0)
                browser.click('//div[@id="agent-detail"]//button[text()="Read cited source"]', using="xpath")
                browser.wait('return document.querySelector("#agent-source").textContent.includes("SOURCE-MARKER")')
                href = browser.inspect('return document.querySelector("#agent-source a").getAttribute("href")')
                self.assertIn(f'/files/{job["file_id"]}/versions/{job["version_id"]}/content', href)
                browser.click('//div[@id="agent-detail"]//button[text()="Load / refresh tool calls"]', using="xpath")
                browser.wait('return document.querySelectorAll("#agent-audit details").length === 3')
                audit = browser.inspect('return document.querySelector("#agent-audit").textContent')
                self.assertIn("Search documents", audit)
                self.assertIn("Read document version", audit)
                self.assertIn("Invalid request", audit)
                self.assertIn("query bytes: 6", audit)
                self.assertIn("selector: chunk", audit)
                self.assertNotIn("SOURCE", audit)
                self.assertNotIn("DO-NOT-LOG", audit)
                browser.click("#agent-audit summary")
                self.assertTrue(browser.inspect('return document.querySelector("#agent-audit details").open'))

                for width in (360, 375, 390, 414, 768, 1280):
                    browser.command("POST", "/goog/cdp/execute", {"cmd": "Emulation.setDeviceMetricsOverride",
                        "params": {"width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": False}})
                    geometry = browser.inspect('''const p=document.querySelector('#agent-workspace');
                        return {width:innerWidth, overflow:p.scrollWidth>p.clientWidth,
                        small:[...p.querySelectorAll('button, textarea, summary')].some(n=>n.getBoundingClientRect().height<44)};''')
                    self.assertEqual(geometry, {"width": width, "overflow": False, "small": False})
                    if width in (360, 1280):
                        raw = browser.command("GET", "/element/"+browser.find("#agent-workspace")+"/screenshot")
                        (output/f"agent-{width}.png").write_bytes(base64.b64decode(raw, validate=True))
                        raw = browser.command("GET", "/element/"+browser.find("#agent-audit")+"/screenshot")
                        (output/f"audit-{width}.png").write_bytes(base64.b64decode(raw, validate=True))
                self.policy(job, False)
                browser.click('//div[@id="agent-detail"]//button[text()="Load / refresh tool calls"]', using="xpath")
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("task_scope_changed")')
                self.assertEqual(browser.inspect('return document.querySelector("#agent-detail").children.length + document.querySelector("#agent-source").children.length + document.querySelector("#agent-audit").children.length'), 0)
                # The business API drives confirmation/execution here; this is not a claim
                # that the future confirmation UI or a model initiated these operations.
                self.policy(job, True)
                base = f'/api/v1/projects/{self.project["id"]}'
                target = data(self.user.request("POST", base+"/directories", {
                    "parent_id": self.project["root_directory_id"], "name": "Archive target"}), 201)
                data(self.user.request("PUT", base+"/archive-policy", {"expected_revision": 0,
                    "targets": [{"directory_id": target["id"], "name": target["name"]}], "confirm_remote_ai": True}))
                archive = self.create_task(job)
                archive_path = self.tasks_path()+"/"+archive["id"]+"/archive-plan"
                preview = data(self.user.request("PUT", archive_path, {"expected_revision": 0, "policy_revision": 1, "items": [{
                    "file_id": job["file_id"], "version_id": job["version_id"], "source_directory_id": self.project["root_directory_id"],
                    "target_directory_id": target["id"], "reason": "Browser fixture"}]}))
                browser.click("#agent-refresh")
                browser.wait('return document.querySelector("#agent-list").textContent.includes("waiting_confirmation")')
                browser.click("#agent-list button")
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("Waiting for archive confirmation")')
                confirmed = data(self.user.request("POST", archive_path+"/decision", {"plan_id": preview["id"],
                    "revision": preview["revision"], "content_sha256": preview["content_sha256"], "decision": "confirm"}))
                browser.click('//div[@id="agent-detail"]//button[text()="Refresh task"]', using="xpath")
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("Waiting for explicit archive execution")')
                self.assertTrue(browser.inspect('return [...document.querySelectorAll("#agent-detail button")].some(b => b.textContent === "Cancel task")'))
                data(self.user.request("POST", archive_path+"/execute", {"plan_id": preview["id"],
                    "revision": preview["revision"], "operation_id": confirmed["operation_id"]}))
                browser.click('//div[@id="agent-detail"]//button[text()="Refresh task"]', using="xpath")
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("Archive operation committed")')
                self.assertIn("1 files", browser.inspect('return document.querySelector("#agent-detail").textContent'))
                for width in (360, 375, 390, 414, 768, 1280):
                    browser.command("POST", "/goog/cdp/execute", {"cmd": "Emulation.setDeviceMetricsOverride",
                        "params": {"width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": False}})
                    geometry = browser.inspect('''const p=document.querySelector('#agent-workspace');
                        return {width:innerWidth, overflow:p.scrollWidth>p.clientWidth,
                        small:[...p.querySelectorAll('button, textarea')].some(n=>n.getBoundingClientRect().height<44)};''')
                    self.assertEqual(geometry, {"width": width, "overflow": False, "small": False})
                    if width in (360, 1280):
                        raw = browser.command("GET", "/element/"+browser.find("#agent-workspace")+"/screenshot")
                        (output/f"archive-{width}.png").write_bytes(base64.b64decode(raw, validate=True))
                browser.click("#logout-button")
                sign_in("agent-browser-reader")
                self.assertEqual(browser.inspect('return document.querySelector("#agent-list").children.length'), 0)
                self.assertEqual(browser.inspect('return document.querySelector("#agent-question").value'), "")
                print("PASS Chrome/C++/MySQL: task lifecycle, answer/citation/audit, revocation clears all; API-confirmed archive waiting states and committed receipt; owner isolation; six viewports including 44px controls. No model call; no confirmation UI claim.", flush=True)
            finally:
                browser.stop()


if __name__ == "__main__":
    suite = unittest.TestSuite([AgentBrowser("test_browser_task_lifecycle_and_fixed_citations")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
