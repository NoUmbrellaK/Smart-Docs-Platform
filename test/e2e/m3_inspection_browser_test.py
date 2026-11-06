"""Real Chrome/C++/isolated SQL inspection UI; no model or external network access."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

from m1_browser_test import Browser, dependencies
from m1_http_test import data
from m3_inspection_constraints_test import InspectionConstraintsE2E


class InspectionBrowserE2E(InspectionConstraintsE2E):
    def test_inspection_browser_explicit_choice_reset_detail_and_mobile_layout(self):
        job=self.approved_file()
        other=data(self.user.request('POST','/api/v1/projects',{'name':'Other inspection project'}),201)
        output=Path(os.environ['SMARTDOCS_INSPECTION_BROWSER_OUTPUT'])
        output.mkdir(parents=True,exist_ok=True)
        binaries,sandbox=dependencies(version_timeout=120)
        self.assertEqual(sandbox,'enabled')
        with tempfile.TemporaryDirectory(prefix='smartdocs-inspection-browser-') as profile:
            browser=Browser(binaries,sandbox,profile,driver_log=output/'chromedriver.log')
            try:
                browser.start()
                browser.command('POST','/url',{'url':f'http://127.0.0.1:{self.server.port}/app.html'})
                browser.wait('return !document.querySelector("#login-form").hidden && document.querySelector("#global-status").textContent === "idle"')
                browser.fill('#username','worker-e2e-admin'); browser.fill('#password',self.password)
                browser.click('#login-form button[type="submit"]')
                browser.wait('return document.querySelector("#global-status").textContent === "idle" && !document.querySelector("#agent-workspace").hidden')
                browser.click('#project-select option[value="'+self.project['id']+'"]')
                browser.wait('return document.querySelector("#global-status").textContent === "idle" && document.querySelectorAll("#file-rows button").length > 0')

                def add_version():
                    browser.click('#file-rows button')
                    browser.wait('return [...document.querySelectorAll("#version-list button")].some(button=>button.textContent === "Add current version to Agent task")')
                    browser.click('//div[@id="version-list"]//button[text()="Add current version to Agent task"]',using='xpath')
                    browser.wait('return document.querySelector("#agent-scope").children.length === 1')

                def fill_draft(question):
                    add_version(); browser.fill('#agent-question',question)

                fill_draft('Inspect only the selected metadata pages')
                self.assertFalse(browser.inspect('return document.querySelector("#agent-inspection-enabled").checked'))
                self.assertEqual(browser.inspect('return [document.querySelector("#agent-inspection-max-page").value, document.querySelector("#agent-inspection-page-size").value]'),['1','10'])
                self.assertTrue(browser.inspect('return document.querySelector("#agent-inspection-max-page").disabled && document.querySelector("#agent-inspection-page-size").disabled'))
                browser.click('#agent-inspection-enabled')
                browser.fill('#agent-inspection-max-page','2'); browser.fill('#agent-inspection-page-size','3')
                browser.click('#agent-consent')
                browser.fill('#agent-inspection-max-page','4')
                self.assertFalse(browser.inspect('return document.querySelector("#agent-consent").checked'))
                browser.click('#agent-consent'); browser.click('#agent-create')
                browser.wait('return document.querySelector("#agent-create-status").textContent.includes("Created task")')
                constrained=data(self.user.request('GET',self.tasks_path()))['items'][0]
                self.assertEqual(constrained['inspection_constraints'],
                                 {'mode':'metadata','max_page':4,'page_size':3})
                self.assertFalse(browser.inspect('return document.querySelector("#agent-inspection-enabled").checked'))
                self.assertEqual(browser.inspect('return [document.querySelector("#agent-inspection-max-page").value, document.querySelector("#agent-inspection-page-size").value]'),['1','10'])

                browser.click('#agent-list button')
                browser.wait('return document.querySelector("#agent-detail").textContent.includes("Metadata search only")')
                detail=browser.inspect('return document.querySelector("#agent-detail").textContent')
                self.assertIn('Maximum page 4',detail); self.assertIn('Page size limit 3',detail)
                self.assertEqual(browser.inspect('return document.querySelectorAll("#agent-detail script, #agent-detail img").length'),0)

                browser.click('#agent-new-draft'); browser.click('#agent-inspection-enabled')
                browser.fill('#agent-inspection-max-page','6'); browser.fill('#agent-inspection-page-size','7')
                browser.click('#project-select option[value="'+other['id']+'"]')
                browser.wait('return document.querySelector("#global-status").textContent === "idle"')
                self.assertFalse(browser.inspect('return document.querySelector("#agent-inspection-enabled").checked'))
                self.assertEqual(browser.inspect('return [document.querySelector("#agent-inspection-max-page").value, document.querySelector("#agent-inspection-page-size").value]'),['1','10'])

                browser.click('#project-select option[value="'+self.project['id']+'"]')
                browser.wait('return document.querySelector("#global-status").textContent === "idle" && document.querySelectorAll("#file-rows button").length > 0')
                fill_draft('Legacy task without explicit inspection limits')
                browser.click('#agent-consent'); browser.click('#agent-create')
                browser.wait('return document.querySelector("#agent-create-status").textContent.includes("Created task")')
                legacy=data(self.user.request('GET',self.tasks_path()))['items'][0]
                self.assertNotIn('inspection_constraints',legacy)

                browser.click('#agent-new-draft'); fill_draft('Reject invalid local limits')
                browser.click('#agent-inspection-enabled'); browser.fill('#agent-inspection-max-page','1001')
                self.assertFalse(browser.inspect('return document.querySelector("#agent-inspection-max-page").checkValidity()'))
                browser.click('#agent-consent'); browser.click('#agent-create')
                self.assertNotIn('Creating pending task',browser.inspect(
                    'return document.querySelector("#agent-create-status").textContent'))
                self.assertEqual(len(data(self.user.request('GET',self.tasks_path()))['items']),2)

                for width in (360,390,414,768,1280):
                    browser.command('POST','/goog/cdp/execute',{'cmd':'Emulation.setDeviceMetricsOverride',
                        'params':{'width':width,'height':1000,'deviceScaleFactor':1,'mobile':False}})
                    geometry=browser.inspect('''const form=document.querySelector('#agent-form');
                        const visible=node=>node.getClientRects().length;
                        return {width:innerWidth,overflow:form.scrollWidth>form.clientWidth,
                          small:[...form.querySelectorAll('button, input, select, textarea, .checkbox-label')]
                            .filter(node=>visible(node) && !(node.matches('input[type=checkbox]')))
                            .some(node=>node.getBoundingClientRect().height<44)};''')
                    self.assertEqual(geometry,{'width':width,'overflow':False,'small':False})
                    if width in (360,1280):
                        browser.inspect('document.querySelector("#agent-inspection-fields").scrollIntoView();return true;')
                        raw=browser.command('GET','/screenshot')
                        (output/f'inspection-{width}.png').write_bytes(base64.b64decode(raw,validate=True))
            finally:
                browser.stop()


if __name__=='__main__':
    suite=unittest.TestSuite([InspectionBrowserE2E(
        'test_inspection_browser_explicit_choice_reset_detail_and_mobile_layout')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
