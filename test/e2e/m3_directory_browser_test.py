"""Directory administration through real Chrome/C++/isolated SQL; no model."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

from m1_browser_test import Browser, dependencies
from m1_http_test import data
from m3_handover_context_test import HandoverContextE2E


class DirectoryBrowser(HandoverContextE2E):
    def test_directory_create_rename_revokes_approval_without_moving_files(self):
        job = self.approved_file()
        second = self.upload('Second.txt', b'Synthetic second file', 'text/plain')
        original = self.file(job)
        _, reader = self.member('reader')
        output = Path(os.environ['SMARTDOCS_DIRECTORY_BROWSER_OUTPUT'])
        binaries, sandbox = dependencies(version_timeout=120)
        self.assertEqual(sandbox, 'enabled')
        with tempfile.TemporaryDirectory(prefix='smartdocs-directory-browser-') as profile:
            browser = Browser(binaries, sandbox, profile, driver_log=output/'webdriver.log')
            try:
                browser.start()
                browser.command('POST', '/url', {'url': f'http://127.0.0.1:{self.server.port}/app.html'})

                def sign_in(actor):
                    name = data(actor.request('GET', '/api/v1/me'))['user']['username']
                    browser.wait('return !document.querySelector("#login-form").hidden && document.querySelector("#global-status").textContent === "idle"')
                    browser.fill('#username', name); browser.fill('#password', self.password)
                    browser.click('#login-form button[type=submit]')
                    browser.wait('return document.querySelector("#agent-list-status").textContent.includes("tasks on this page")')

                def directories():
                    return data(self.user.request('GET', self.base()+'/directories'))['items']

                def create(parent, name):
                    browser.click('//select[@id="settings-directory-parent"]/option[@value="'+parent+'"]', using='xpath')
                    browser.fill('#settings-directory-name', name)
                    browser.click('#settings-directory-form button[type=submit]')
                    browser.wait('return document.querySelector("#settings-directory-status").textContent.includes("Directory created.") && !document.querySelector("#settings-directory-editor").disabled')
                    return next(d for d in directories() if d['parent_id'] == parent and d['name'] == name)

                sign_in(reader)
                self.assertTrue(browser.inspect('return document.querySelector("#project-settings").hidden'))
                self.assertEqual(reader.request('POST', self.base()+'/directories',
                    {'parent_id': self.project['root_directory_id'], 'name': 'Forbidden'}).status, 403)
                browser.click('#logout-button'); sign_in(self.user)
                browser.click('#settings-directory-refresh')
                browser.wait('return !document.querySelector("#settings-directory-editor").disabled')
                self.assertEqual(browser.inspect('return document.querySelectorAll("#settings-directory-rows form").length'), 0)
                target = create(self.project['root_directory_id'], '<img>Deployment')
                nested = create(target['id'], 'Nested child')
                self.assertEqual(nested['parent_id'], target['id'])
                self.assertEqual(len(directories()), 3)
                self.assertEqual(reader.request('PATCH', self.base()+'/directories/'+target['id'], {'name': 'Forbidden'}).status, 403)
                self.assertEqual(browser.inspect('return document.querySelectorAll("#directory-manager img, #directory-manager script").length'), 0)

                data(self.user.request('PUT', self.base()+'/archive-policy', self.settings(target)))
                task = data(self.user.request('POST', self.tasks_path(), {
                    'question': 'Propose archive only', 'confirm_remote_ai': True,
                    'versions': [{'file_id': job['file_id'], 'version_id': job['version_id']}],
                    'archive_policy_revision': 1}), 201)
                browser.click('#settings-archive-refresh')
                browser.wait('return document.querySelector("#settings-archive-status").textContent.includes("Revision 1 · Currently approved")')
                browser.click('//tbody[@id="file-rows"]//tr[td[contains(., "PRIVATE-FILENAME.txt")]]//button[1]', using='xpath')
                browser.wait('return [...document.querySelectorAll("#version-list button")].some(b=>b.textContent === "Add current version to Agent task")')
                browser.click('//div[@id="version-list"]//button[text()="Add current version to Agent task"]', using='xpath')
                browser.click('#agent-mode option[value=archive]'); browser.click('#agent-context-refresh')
                browser.wait('return document.querySelector("#agent-context").textContent.includes("Revision 1")')
                browser.click('#upload-mode option[value=create_version]')
                browser.click('//select[@id="upload-file-target"]/option[@value="'+second['file_id']+'"]', using='xpath')
                browser.click('#upload-mode option[value=create_file]')
                for selector in ('upload-directory', 'file-directory'):
                    browser.click('//select[@id="'+selector+'"]//option[@value="'+target['id']+'"]', using='xpath')
                card = '//div[@id="settings-directory-rows"]/div[p[starts-with(., "<img>Deployment ·")]]'
                browser.fill(card+'//input', 'Renamed deployment', using='xpath')
                browser.click(card+'//button', using='xpath')
                browser.wait('return document.querySelector("#settings-directory-status").textContent.includes("Directory renamed.") && !document.querySelector("#settings-directory-editor").disabled')
                policy = data(self.user.request('GET', self.base()+'/archive-policy'))
                self.assertEqual((policy['revision'], policy['remote_ai_approved']), (2, False))
                self.assertEqual(self.user.request('POST', self.tasks_path()+'/'+task['id']+'/run/start').status, 409)
                self.assertNotIn('Currently approved', browser.inspect('return document.querySelector("#settings-archive-status").textContent'))
                self.assertEqual(browser.inspect('return document.querySelector("#settings-archive-rows").children.length'), 0)
                self.assertEqual(browser.inspect('return document.querySelector("#agent-mode").value'), 'question')
                self.assertEqual(browser.inspect('return document.querySelector("#agent-context").children.length + document.querySelector("#agent-scope").children.length'), 0)
                for selector in ('upload-directory', 'file-directory'):
                    self.assertEqual(browser.inspect('return document.querySelector(arguments[0]).value', '#'+selector), target['id'])
                self.assertEqual(browser.inspect('return document.querySelector("#upload-file-target").value'), second['file_id'])
                self.assertEqual(self.file(job), original)

                for width in (360, 375, 390, 414, 768, 1280):
                    browser.command('POST', '/goog/cdp/execute', {'cmd': 'Emulation.setDeviceMetricsOverride',
                        'params': {'width': width, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False}})
                    self.assertEqual(browser.inspect('''const p=document.querySelector('#directory-manager');
                        return {width:innerWidth,overflow:p.scrollWidth>p.clientWidth,
                        small:[...p.querySelectorAll('button, input, select')].filter(n=>n.getClientRects().length)
                          .some(n=>n.getBoundingClientRect().height<44)};'''),
                        {'width': width, 'overflow': False, 'small': False})
                    if width in (360, 1280):
                        browser.inspect('document.querySelector("#directory-manager").scrollIntoView();return true;')
                        (output/f'directory-{width}.png').write_bytes(base64.b64decode(browser.command('GET', '/screenshot'), validate=True))
                browser.click('#settings-archive-refresh')
                browser.wait('return document.querySelector("#settings-archive-status").textContent.includes("Revision 2 · Currently not approved")')
                self.assertIn('Renamed deployment', browser.inspect('return document.querySelector("#settings-archive-rows").textContent'))
                browser.click('#logout-button')
                browser.wait('return document.querySelector("#signed-in").hidden')
                self.assertEqual(browser.inspect('return document.querySelector("#settings-directory-rows").children.length'), 0)
                self.assertEqual(self.count('SELECT COUNT(*) FROM archive_plans'), 0)
                self.assertEqual(self.count('SELECT COUNT(*) FROM agent_runs WHERE model_calls > 0'), 0)
            finally:
                browser.stop()


if __name__ == '__main__':
    suite = unittest.TestSuite([DirectoryBrowser('test_directory_create_rename_revokes_approval_without_moving_files')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
