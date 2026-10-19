"""Real Chrome/C++/SQL report UI; reports are protocol fixtures, not model output."""
import base64
import os
from pathlib import Path
import tempfile
import time
import unittest

from m1_browser_test import Browser, dependencies
from m1_http_test import data
from m3_handover_report_test import HandoverReportE2E


class ReportBrowser(HandoverReportE2E):
    def checklist(self, expected=0, approved=True, items=None):
        return super().checklist(expected, approved, items or [
            {'key':'deployment','title':'Deployment 部署','description':'Public fixture','required':True},
            {'key':'tests','title':'Tests 测试','description':'Public fixture','required':True},
            {'key':'notes','title':'Notes 待确认','description':'Public fixture','required':False}])

    def test_browser_private_report_sources_download_and_revocation(self):
        jobs,task,path,body,actor=self.prepared_report(reader=True)
        body['report']['items'] += [
            {'key':'tests','status':'missing','notes':'Not found within searched scope.','citations':[]},
            {'key':'notes','status':'uncertain','notes':'Evidence insufficient.','citations':[]}]
        data(actor.request('PUT',path+'/handover-report',body))
        username=data(actor.request('GET','/api/v1/me'))['user']['username']
        output=Path(os.environ['SMARTDOCS_REPORT_BROWSER_OUTPUT']);output.mkdir(parents=True,exist_ok=True)
        binaries,sandbox=dependencies(version_timeout=120);self.assertEqual(sandbox,'enabled')
        with tempfile.TemporaryDirectory(prefix='smartdocs-report-browser-') as profile, \
                tempfile.TemporaryDirectory(prefix='smartdocs-report-download-') as download:
            browser=Browser(binaries,sandbox,profile)
            try:
                browser.start()
                browser.command('POST','/goog/cdp/execute',{'cmd':'Browser.setDownloadBehavior',
                    'params':{'behavior':'allow','downloadPath':download}})
                browser.command('POST','/url',{'url':f'http://127.0.0.1:{self.server.port}/app.html'})
                def sign_in(name):
                    browser.wait('return !document.querySelector("#login-form").hidden && document.querySelector("#global-status").textContent === "idle"')
                    browser.fill('#username',name);browser.fill('#password',self.password)
                    browser.click('#login-form button[type="submit"]')
                    browser.wait('return document.querySelector("#agent-list-status").textContent.includes("tasks on this page")')
                def click(label):
                    browser.click('//div[@id="agent-detail"]//button[text()="'+label+'"]',using='xpath')
                def open_report():
                    browser.click('#agent-refresh')
                    browser.wait('return document.querySelector("#agent-list").textContent.includes(arguments[0])',task['id'])
                    browser.click('#agent-list button')
                    browser.wait('return document.querySelector("#agent-detail").textContent.includes("REPORT-MARKER")')

                sign_in(username);open_report()
                self.assertIn('handover report',browser.inspect('return document.querySelector("#agent-detail-status").textContent'))
                content=browser.inspect('return document.querySelector("#agent-detail").textContent')
                for marker in ('Found','Missing within searched scope','Uncertain','Required','Optional',
                               'Actual admitted sources','not semantically reviewed','Evidence found.txt','<script>alert(1)</script>'):
                    self.assertIn(marker,content)
                self.assertEqual(browser.inspect('return document.querySelectorAll("#agent-detail script, #agent-detail img").length'),0)
                click('Read cited source')
                browser.wait('return document.querySelector("#agent-source").textContent.includes("Evidence found.txt")')
                self.assertIn('/versions/'+jobs[0]['version_id']+'/content',browser.inspect('return document.querySelector("#agent-source a").getAttribute("href")'))

                for width in (360,375,390,414,768,1280):
                    browser.command('POST','/goog/cdp/execute',{'cmd':'Emulation.setDeviceMetricsOverride',
                        'params':{'width':width,'height':1000,'deviceScaleFactor':1,'mobile':False}})
                    geometry=browser.inspect('''const p=document.querySelector('#agent-detail');
                        return {width:innerWidth,overflow:p.scrollWidth>p.clientWidth,
                        small:[...p.querySelectorAll('button')].some(n=>n.getBoundingClientRect().height<44)};''')
                    self.assertEqual(geometry,{'width':width,'overflow':False,'small':False})
                    if width in (360,1280):
                        browser.inspect('document.querySelector("#agent-detail h3").scrollIntoView();return true;')
                        raw=browser.command('GET','/screenshot')
                        (output/f'report-{width}.png').write_bytes(base64.b64decode(raw,validate=True))
                click('Download report Markdown')
                downloaded=Path(download)/'handover-report.md';deadline=time.monotonic()+30
                while not downloaded.exists() and time.monotonic()<deadline: time.sleep(0.2)
                self.assertTrue(downloaded.exists(),'browser download did not complete')
                markdown=downloaded.read_text();self.assertIn('REPORT',markdown)
                self.assertIn(task['id'],markdown);self.assertNotIn('<script>',markdown)
                self.assertEqual(downloaded.read_bytes(),actor.request('GET',path+'/handover-report/markdown').body)

                # Uncited but model-admitted metadata must gate downloads and the whole visible report.
                self.policy(jobs[1],False)
                click('Download report Markdown')
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("task_scope_changed")')
                self.assertEqual(browser.inspect('return document.querySelector("#agent-detail").children.length + document.querySelector("#agent-source").children.length'),0)
                self.assertEqual(len(list(Path(download).glob('*.md'))),1)
                self.policy(jobs[1],True);open_report();self.policy(jobs[1],False)
                click('Read cited source')
                browser.wait('return document.querySelector("#agent-detail").children.length === 0')
                self.assertEqual(browser.inspect('return document.querySelector("#agent-source").children.length'),0)
                browser.click('#logout-button');sign_in('worker-e2e-admin')
                self.assertNotIn(task['id'],browser.inspect('return document.querySelector("#agent-list").textContent'))
                self.assertEqual(self.user.request('GET',path+'/handover-report').status,404)
                self.assertEqual(self.user.request('GET',path+'/handover-report/markdown').status,404)
            finally: browser.stop()


if __name__=='__main__':
    suite=unittest.TestSuite([ReportBrowser('test_browser_private_report_sources_download_and_revocation')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
