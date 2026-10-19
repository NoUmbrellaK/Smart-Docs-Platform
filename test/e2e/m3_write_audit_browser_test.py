"""Real UI rendering of write traces; protocol fixtures, not remote-model output."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

from m1_browser_test import Browser, dependencies
from m1_http_test import data
from m3_write_tool_audit_test import WriteToolAuditE2E
from smartdocs import worker


class WriteAuditBrowser(WriteToolAuditE2E):
    def test_browser_write_traces_are_redacted_and_read_only(self):
        jobs, _, archive, path, _, plan = self.prepared()
        self.assertEqual(self.user.request('POST', path+'/execute', self.execution(plan)).status, 409)
        command = self.execution(self.confirm(path, plan))
        self.fault('UPDATE')
        try:
            self.assertEqual(self.user.request('POST', path+'/execute', command).status, 503)
        finally:
            self.sql('DROP TRIGGER write_tool_audit_fault')
        self.assertEqual(worker.run_once(self.client), 'completed')
        _, report, report_path, report_body, actor = self.prepared_report()
        data(actor.request('PUT', report_path+'/handover-report', report_body))
        archive_path = self.tasks_path()+'/'+archive['id']
        before = self.trace(archive_path)
        report_before = self.trace(report_path)
        binaries, sandbox = dependencies(version_timeout=120)
        self.assertEqual(sandbox, 'enabled')
        output = Path(os.environ['SMARTDOCS_WRITE_AUDIT_BROWSER_OUTPUT'])
        with tempfile.TemporaryDirectory(prefix='smartdocs-write-audit-browser-') as profile:
            browser = Browser(binaries, sandbox, profile, driver_log=output/'webdriver.log')
            try:
                browser.start()
                browser.command('POST', '/url', {'url': f'http://127.0.0.1:{self.server.port}/app.html'})
                browser.wait('return document.querySelector("#global-status").textContent === "idle"')
                browser.fill('#username', 'worker-e2e-admin'); browser.fill('#password', self.password)
                browser.click('#login-form button[type=submit]')
                browser.wait('return document.querySelector("#agent-list-status").textContent.includes("tasks on this page")')

                def view(task_id, count):
                    browser.click('//div[@id="agent-list"]/article[p[contains(., "'+task_id+'")]]/button', using='xpath')
                    browser.wait('return document.querySelector("#agent-detail-status").textContent.includes(arguments[0])', task_id)
                    browser.click('//div[@id="agent-detail"]//button[text()="Load / refresh tool calls"]', using='xpath')
                    browser.wait('return document.querySelectorAll("#agent-audit details").length === arguments[0]', count)
                    return browser.inspect('return document.querySelector("#agent-audit").textContent')

                content = view(archive['id'], 3)
                for expected in ('Create archive plan', 'Waiting for confirmation; no files moved', 'Execute archive plan', 'Conflict', 'Outcome unknown'):
                    self.assertIn(expected, content)
                self.assertNotIn('Deployment material', content)
                self.assertNotIn(command['operation_id'], content)
                browser.click('#agent-audit summary')
                self.assertTrue(browser.inspect('return document.querySelector("#agent-audit details").open'))
                for width in (360, 375, 390, 414, 768, 1280):
                    browser.command('POST', '/goog/cdp/execute', {'cmd': 'Emulation.setDeviceMetricsOverride',
                        'params': {'width': width, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False}})
                    self.assertEqual(browser.inspect('''const p=document.querySelector('#agent-audit');
                        return {width:innerWidth,overflow:p.scrollWidth>p.clientWidth,
                        small:[...p.querySelectorAll('summary')].some(n=>n.getBoundingClientRect().height<44)};'''),
                        {'width': width, 'overflow': False, 'small': False})
                    if width in (360, 1280):
                        raw = browser.command('GET', '/element/'+browser.find('#agent-audit')+'/screenshot')
                        (output/f'write-audit-{width}.png').write_bytes(base64.b64decode(raw, validate=True))
                content = view(report['id'], 3)
                self.assertIn('Save handover report', content)
                self.assertIn('items: 1', content)
                self.assertNotIn('REPORT-MARKER', content)
                self.assertEqual(self.trace(archive_path), before)
                self.assertEqual(self.trace(report_path), report_before)
                self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan['id']+"'"), 1)
                self.policy(jobs[0], False)
                # A committed minimal receipt remains readable; protected trace must recheck scope.
                browser.click('//div[@id="agent-list"]/article[p[contains(., "'+archive['id']+'")]]/button', using='xpath')
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes(arguments[0])', archive['id'])
                browser.click('//div[@id="agent-detail"]//button[text()="Load / refresh tool calls"]', using='xpath')
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("task_scope_changed")')
                self.assertEqual(browser.inspect('return document.querySelector("#agent-audit").children.length'), 0)
            finally:
                browser.stop()


if __name__ == '__main__':
    suite = unittest.TestSuite([WriteAuditBrowser('test_browser_write_traces_are_redacted_and_read_only')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
