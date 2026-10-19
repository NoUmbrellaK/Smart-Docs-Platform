"""Real Chrome/C++/isolated SQL task modes. No model invocation or production data."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

from m1_browser_test import Browser, dependencies
from m1_http_test import data
from m3_handover_context_test import HandoverContextE2E


class TaskModeBrowser(HandoverContextE2E):
    def test_browser_explicit_modes_revision_conflicts_and_roles(self):
        job = self.approved_file()
        _, reader = self.member('reader')
        editor_id, editor = self.member('editor')
        target = self.directory('Approved deployment destination')
        data(self.user.request('PUT', self.base()+'/archive-policy', self.settings(target)))
        output = Path(os.environ['SMARTDOCS_TASK_MODE_BROWSER_OUTPUT'])
        output.mkdir(parents=True, exist_ok=True)
        binaries, sandbox = dependencies(version_timeout=120)
        self.assertEqual(sandbox, 'enabled')
        with tempfile.TemporaryDirectory(prefix='smartdocs-mode-browser-') as profile:
            browser = Browser(binaries, sandbox, profile)
            try:
                browser.start()
                browser.command('POST', '/url', {'url': f'http://127.0.0.1:{self.server.port}/app.html'})

                def sign_in(actor):
                    name = data(actor.request('GET', '/api/v1/me'))['user']['username']
                    browser.wait('return !document.querySelector("#login-form").hidden && document.querySelector("#global-status").textContent === "idle"')
                    browser.fill('#username', name); browser.fill('#password', self.password)
                    browser.click('#login-form button[type="submit"]')
                    browser.wait('return document.querySelector("#agent-list-status").textContent.includes("tasks on this page")')

                def choose(value):
                    browser.click('#agent-mode option[value="'+value+'"]')
                    self.assertEqual(browser.inspect('return document.querySelector("#agent-mode").value'), value)
                    self.assertFalse(browser.inspect('return document.querySelector("#agent-consent").checked'))

                def draft(value):
                    browser.click('#agent-new-draft')
                    browser.click('#file-rows button')
                    browser.wait('return [...document.querySelectorAll("#version-list button")].some(b=>b.textContent === "Add current version to Agent task")')
                    browser.click('//div[@id="version-list"]//button[text()="Add current version to Agent task"]', using='xpath')
                    browser.fill('#agent-question', 'Check these synthetic materials')
                    choose(value)

                def load():
                    browser.click('#agent-context-refresh')
                    browser.wait('return document.querySelector("#agent-context-status").textContent.includes("Review this configuration")')

                def create(actor, field=None):
                    browser.click('#agent-consent'); browser.click('#agent-create')
                    browser.wait('return document.querySelector("#agent-create-status").textContent.includes("Created task")')
                    tasks = data(actor.request('GET', self.tasks_path()))['items']
                    task = tasks[0]
                    self.assertEqual(task['state'], 'pending')
                    self.assertEqual(task['versions'], [{'file_id': job['file_id'], 'version_id': job['version_id']}])
                    for key in ('handover_checklist_revision', 'archive_policy_revision'):
                        if key == field: self.assertEqual(task[key], 1)
                        else: self.assertNotIn(key, task)
                    return task

                sign_in(reader); draft('handover')
                self.assertTrue(browser.inspect('return document.querySelector("#agent-mode-archive").disabled'))
                browser.click('#agent-context-refresh')
                browser.wait('return document.querySelector("#agent-context-status").textContent.includes("No approved")')
                self.assertTrue(browser.inspect('return document.querySelector("#agent-create").disabled'))
                self.checklist(items=[{'key': 'deploy', 'title': '<script>Deployment</script>',
                    'description': 'Check rollback and configuration. '+'LongSample'*12, 'required': True}])
                load()
                self.assertIn('<script>Deployment</script>', browser.inspect('return document.querySelector("#agent-context").textContent'))
                self.assertEqual(browser.inspect('return document.querySelectorAll("#agent-context script, #agent-context img").length'), 0)
                for width in (360, 375, 390, 414, 768, 1280):
                    browser.command('POST', '/goog/cdp/execute', {'cmd': 'Emulation.setDeviceMetricsOverride',
                        'params': {'width': width, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False}})
                    geometry = browser.inspect('''const p=document.querySelector('#agent-form');
                        return {width:innerWidth,overflow:p.scrollWidth>p.clientWidth,
                        small:[...p.querySelectorAll('button, select, textarea, label')].filter(n=>n.getClientRects().length)
                            .some(n=>n.matches('label:not(.checkbox-label)') ? false : n.getBoundingClientRect().height<44)};''')
                    self.assertEqual(geometry, {'width': width, 'overflow': False, 'small': False})
                    if width in (360, 1280):
                        browser.inspect('document.querySelector("#agent-mode").scrollIntoView();return true;')
                        raw = browser.command('GET', '/screenshot')
                        (output/f'mode-{width}.png').write_bytes(base64.b64decode(raw, validate=True))
                create(reader, 'handover_checklist_revision')
                draft('question'); create(reader)
                draft('handover'); load(); self.checklist(1, approved=False)
                browser.click('#agent-consent'); browser.click('#agent-create')
                browser.wait('return document.querySelector("#agent-create-status").textContent.includes("task_scope_changed")')
                self.assertEqual(len(data(reader.request('GET', self.tasks_path()))['items']), 2)
                self.assertEqual(browser.inspect('return document.querySelector("#agent-context").children.length'), 0)
                choose('question')
                self.assertTrue(browser.inspect('return document.querySelector("#agent-create").disabled'))
                forged = self.body(job); forged.pop('handover_checklist_revision'); forged['archive_policy_revision'] = 1
                self.assertEqual(reader.request('POST', self.tasks_path(), forged).status, 403)

                browser.click('#logout-button'); sign_in(editor); draft('archive'); load()
                self.assertIn(target['name'], browser.inspect('return document.querySelector("#agent-context").textContent'))
                create(editor, 'archive_policy_revision')
                self.assertEqual(self.file(job)['directory_id'], self.project['root_directory_id'])
                self.assertEqual(self.count('SELECT COUNT(*) FROM archive_plans'), 0)
                self.assertEqual(self.count('SELECT COUNT(*) FROM agent_runs'), 0)
                draft('archive'); load()
                data(self.user.request('PUT', self.base()+'/members/'+editor_id, {'role': 'reader'}))
                browser.click('#agent-consent'); browser.click('#agent-create')
                browser.wait('return document.querySelector("#agent-create-status").textContent.includes("forbidden")')
                self.assertEqual(len(data(editor.request('GET', self.tasks_path()))['items']), 1)
                self.assertEqual(browser.inspect('return document.querySelector("#agent-context").children.length'), 0)
                self.assertEqual(self.count('SELECT COUNT(*) FROM agent_tasks'), 3)
            finally:
                browser.stop()


if __name__ == '__main__':
    suite = unittest.TestSuite([TaskModeBrowser('test_browser_explicit_modes_revision_conflicts_and_roles')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
