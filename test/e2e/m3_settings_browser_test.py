"""Admin settings via actual Chrome/C++/isolated SQL; synthetic metadata, no model."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

from m1_browser_test import Browser, dependencies
from m1_http_test import data
from m3_handover_context_test import HandoverContextE2E


class SettingsBrowser(HandoverContextE2E):
    def test_browser_settings_approval_conflicts_and_role_invalidation(self):
        job = self.approved_file()
        _, reader = self.member('reader')
        _, cleanup_admin = self.member('admin')  # Keep an authorized actor for fixture cleanup after self-demotion.
        target = self.directory('<img>Approved deployment')
        output = Path(os.environ['SMARTDOCS_SETTINGS_BROWSER_OUTPUT']); output.mkdir(parents=True, exist_ok=True)
        binaries, sandbox = dependencies(version_timeout=120); self.assertEqual(sandbox, 'enabled')
        with tempfile.TemporaryDirectory(prefix='smartdocs-settings-browser-') as profile:
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

                def load(kind, revision):
                    browser.click('#settings-'+kind+'-refresh')
                    browser.wait('return document.querySelector(arguments[0]).textContent.includes(arguments[1])',
                                 '#settings-'+kind+'-status', f'Revision {revision} ·')
                    self.assertFalse(browser.inspect('return document.querySelector(arguments[0]).checked', '#settings-'+kind+'-approval'))

                def save(kind, revision):
                    browser.click('#settings-'+kind+'-form button[type=submit]')
                    browser.wait('return document.querySelector(arguments[0]).textContent.includes(arguments[1])',
                                 '#settings-'+kind+'-status', f'Saved revision {revision}.')
                    self.assertEqual(browser.inspect('return document.querySelector(arguments[0]).children.length', '#settings-'+kind+'-rows'), 0)

                def select_target(name):
                    browser.click('//div[@id="settings-archive-rows"]//label[span[contains(., "'+name+'")]]/input', using='xpath')

                sign_in(reader)
                self.assertTrue(browser.inspect('return document.querySelector("#project-settings").hidden'))
                self.assertEqual(reader.request('PUT', self.base()+'/handover-checklist',
                    {'expected_revision': 0, 'confirm_remote_ai': True, 'items': []}).status, 403)
                browser.click('#logout-button'); sign_in(self.user)
                load('checklist', 0); browser.click('#settings-checklist-add')
                browser.fill('#settings-checklist-rows input[name=key]', 'deployment')
                browser.fill('#settings-checklist-rows input[name=title]', '<script>Deployment</script>')
                browser.fill('#settings-checklist-rows input[name=description]', 'Check rollback and configuration')
                browser.click('#settings-checklist-approval')
                browser.fill('#settings-checklist-rows input[name=description]', 'Check rollback')
                self.assertFalse(browser.inspect('return document.querySelector("#settings-checklist-approval").checked'))
                browser.click('#settings-checklist-approval'); save('checklist', 1)
                saved = data(self.user.request('GET', self.base()+'/handover-checklist'))
                self.assertEqual(saved, {'revision': 1, 'remote_ai_approved': True, 'items': [
                    {'key': 'deployment', 'title': '<script>Deployment</script>', 'description': 'Check rollback', 'required': True}]})
                task, path = self.task(job)
                load('checklist', 1); load('archive', 0); select_target(target['name'])
                browser.click('#settings-archive-approval'); save('archive', 1)
                self.assertEqual(data(self.user.request('GET', self.base()+'/archive-policy')),
                    {'revision': 1, 'remote_ai_approved': True, 'targets': [{'directory_id': target['id'], 'name': target['name']}]})
                load('archive', 1)
                self.assertEqual(browser.inspect('return document.querySelectorAll("#project-settings script, #project-settings img").length'), 0)

                for width in (360, 375, 390, 414, 768, 1280):
                    browser.command('POST', '/goog/cdp/execute', {'cmd': 'Emulation.setDeviceMetricsOverride',
                        'params': {'width': width, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False}})
                    geometry = browser.inspect('''const p=document.querySelector('#project-settings');
                        return {width:innerWidth,overflow:p.scrollWidth>p.clientWidth,
                        small:[...p.querySelectorAll('button, input:not([type=checkbox]), select, label.checkbox-label')]
                          .filter(n=>n.getClientRects().length).some(n=>n.getBoundingClientRect().height<44),
                        smallCheckbox:[...p.querySelectorAll('input[type=checkbox]')]
                          .filter(n=>n.getClientRects().length).some(n=>{const r=n.getBoundingClientRect();return r.width<20||r.height<20})};''')
                    self.assertEqual(geometry, {'width': width, 'overflow': False, 'small': False, 'smallCheckbox': False})
                    if width in (360, 1280):
                        browser.inspect('document.querySelector("#settings-checklist-refresh").scrollIntoView();return true;')
                        raw = browser.command('GET', '/screenshot')
                        (output/f'settings-{width}.png').write_bytes(base64.b64decode(raw, validate=True))

                # A configuration write invalidates the currently reviewed task draft in the same page.
                browser.click('#file-rows button')
                browser.wait('return [...document.querySelectorAll("#version-list button")].some(b=>b.textContent === "Add current version to Agent task")')
                browser.click('//div[@id="version-list"]//button[text()="Add current version to Agent task"]', using='xpath')
                browser.click('#agent-mode option[value=handover]'); browser.click('#agent-context-refresh')
                browser.wait('return document.querySelector("#agent-context").textContent.includes("Revision 1")')
                save('checklist', 2)  # Deliberately unchecked: revoke existing approval.
                self.assertFalse(data(self.user.request('GET', self.base()+'/handover-checklist'))['remote_ai_approved'])
                self.assertEqual(self.user.request('POST', path+'/run/start').status, 409)
                self.assertEqual(browser.inspect('return document.querySelector("#agent-mode").value'), 'question')
                self.assertEqual(browser.inspect('return document.querySelector("#agent-context").children.length + document.querySelector("#agent-scope").children.length'), 0)

                load('checklist', 2); self.checklist(2)
                browser.click('#settings-checklist-form button[type=submit]')
                browser.wait('return document.querySelector("#settings-checklist-status").textContent.includes("handover_checklist_changed")')
                self.assertEqual(data(self.user.request('GET', self.base()+'/handover-checklist'))['revision'], 3)
                self.assertEqual(browser.inspect('return document.querySelector("#settings-checklist-rows").children.length'), 0)
                data(self.user.request('PATCH', self.base()+'/directories/'+target['id'], {'name': 'Renamed target'}))
                browser.click('#settings-archive-approval'); browser.click('#settings-archive-form button[type=submit]')
                browser.wait('return document.querySelector("#settings-archive-status").textContent.includes("archive_policy_changed")')
                load('archive', 2)
                self.assertIn('Renamed target', browser.inspect('return document.querySelector("#settings-archive-rows").textContent'))
                browser.click('#settings-archive-approval'); save('archive', 3)
                self.assertEqual(data(self.user.request('GET', self.base()+'/archive-policy'))['targets'][0]['name'], 'Renamed target')
                load('archive', 3); select_target('Renamed target'); save('archive', 4)
                self.assertEqual(data(self.user.request('GET', self.base()+'/archive-policy'))['targets'], [])
                load('checklist', 3); browser.click('#settings-checklist-rows button'); save('checklist', 4)
                self.assertEqual(data(self.user.request('GET', self.base()+'/handover-checklist'))['items'], [])
                load('archive', 4); load('checklist', 4)
                uid = data(self.user.request('GET', '/api/v1/me'))['user']['id']
                card = '//div[@id="member-list"]/article[p[contains(., "'+uid+'")]]'
                browser.click(card+'//select/option[@value="reader"]', using='xpath')
                browser.click(card+'//button', using='xpath')
                browser.wait('return document.querySelector("#project-role").textContent === "reader"')
                self.assertTrue(browser.inspect('return document.querySelector("#project-settings").hidden'))
                self.assertEqual(browser.inspect('return document.querySelector("#settings-archive-rows").children.length'), 0)
                self.assertEqual(self.count('SELECT COUNT(*) FROM archive_plans'), 0)
                self.assertEqual(self.count('SELECT COUNT(*) FROM agent_runs WHERE model_calls > 0'), 0)
            finally:
                self.user = cleanup_admin
                browser.stop()


if __name__ == '__main__':
    suite = unittest.TestSuite([SettingsBrowser('test_browser_settings_approval_conflicts_and_role_invalidation')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
