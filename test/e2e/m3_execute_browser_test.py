"""Chrome -> test-only same-origin proxy -> Agent HTTP/stdio MCP/C++/SQL. No paid network."""
import asyncio
import base64
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import unittest

from m1_browser_test import Browser, dependencies
from m1_http_test import data
from m3_agent_http_test import AgentHttpE2E, tool
from smartdocs import worker


@contextmanager
def site(fixture, respond):
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def relay(self):
            if self.path.startswith('/internal/'): self.send_error(404); return
            length = int(self.headers.get('Content-Length', '0'))
            if length > 65536: self.send_error(413); return
            execute = bool(re.fullmatch(r'/api/v1/projects/[0-9a-f]{32}/agent-tasks/[0-9a-f]{32}/execute', self.path))
            if execute: proxy.executions += 1
            upstream = http.client.HTTPConnection('127.0.0.1', fixture.gateway_port if execute else fixture.server.port, timeout=240)
            try:
                headers = {k:self.headers[k] for k in ('Host','Origin','Cookie','Content-Type') if k in self.headers}
                headers['Connection'] = 'close'
                upstream.request(self.command, self.path, self.rfile.read(length) if length else None, headers)
                response = upstream.getresponse(); body = response.read(2*1024*1024+1)
                if len(body) > 2*1024*1024: raise ValueError('test response limit')
                if execute and proxy.lose_response:
                    proxy.lose_response = False
                    self.connection.shutdown(socket.SHUT_RDWR); self.close_connection = True; return
                self.send_response_only(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in ('connection','content-length','transfer-encoding'): self.send_header(key,value)
                self.send_header('Content-Length',str(len(body))); self.send_header('Connection','close')
                self.end_headers(); self.wfile.write(body)
            finally: upstream.close()
        do_GET = do_POST = relay
    with ThreadingHTTPServer(('127.0.0.1',0),Proxy) as proxy:
        proxy.executions, proxy.lose_response = 0, False
        origin = f'http://127.0.0.1:{proxy.server_port}'
        with fixture.gateway(respond, origin=origin):
            thread = threading.Thread(target=proxy.serve_forever); thread.start()
            try: yield origin, proxy
            finally: proxy.shutdown(); proxy.server_close(); thread.join(5)


class ExecuteBrowser(AgentHttpE2E):
    def test_browser_explicit_execution_lost_response_and_cancel(self):
        job = self.approved_file(); self.assertEqual(worker.run_once(self.client),'completed')
        entered, release = threading.Event(), threading.Event()
        async def respond(body):
            messages = body['messages']
            if messages[1]['content'] == 'Cancel this synthetic task':
                entered.set()
                if not await asyncio.to_thread(release.wait,40): raise AssertionError('test release timeout')
                return tool('search_documents',{'mode':'metadata','query':''})
            if messages[-1]['role'] != 'tool':
                return tool('read_document_version',{k:job[k] for k in ('file_id','version_id')} | {'range':{}})
            chunk = json.loads(messages[-1]['content'])['data']['chunks'][0]
            return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({
                'answer':'Synthetic cited answer <img onerror=bad>',
                'citations':[{k:chunk[k] for k in ('file_id','version_id','chunk_id')}]})}}]}
        output = Path(os.environ['SMARTDOCS_EXECUTE_BROWSER_OUTPUT']); output.mkdir(parents=True,exist_ok=True)
        binaries, sandbox = dependencies(version_timeout=120); self.assertEqual(sandbox,'enabled')
        with site(self,respond) as (origin,proxy), tempfile.TemporaryDirectory(prefix='smartdocs-execute-browser-') as profile:
            browser = Browser(binaries,sandbox,profile,driver_log=output/'webdriver.log')
            try:
                browser.start(); browser.command('POST','/url',{'url':origin+'/app.html'})
                browser.wait('return document.querySelector("#global-status").textContent === "idle"')
                browser.fill('#username','worker-e2e-admin'); browser.fill('#password',self.password)
                browser.click('#login-form button[type=submit]')
                browser.wait('return document.querySelector("#agent-list-status").textContent.includes("tasks on this page")')
                def click(label): browser.click('//div[@id="agent-detail"]//button[text()="'+label+'"]',using='xpath')
                def create(question):
                    browser.click('#file-rows button')
                    browser.wait('return [...document.querySelectorAll("#version-list button")].some(b=>b.textContent==="Add current version to Agent task")')
                    browser.click('//div[@id="version-list"]//button[text()="Add current version to Agent task"]',using='xpath')
                    browser.fill('#agent-question',question); browser.click('#agent-consent'); browser.click('#agent-create')
                    browser.wait('return document.querySelector("#agent-create-status").textContent.includes("Created task")')
                    browser.wait('return document.querySelectorAll("#agent-list button").length>0')
                    task = data(self.user.request('GET',self.tasks_path()))['items'][0]
                    self.assertEqual(task['state'],'pending')
                    browser.click('#agent-list button')
                    browser.wait('return !!document.querySelector("#agent-detail input[type=checkbox]")')
                    self.assertFalse(browser.inspect('return document.querySelector("#agent-detail input").checked'))
                    self.assertTrue(browser.inspect('return [...document.querySelectorAll("#agent-detail button")].find(b=>b.textContent==="Start this task").disabled'))
                    return task
                first = create('Read the synthetic source')
                self.assertEqual(proxy.executions,0); self.assertEqual(self.calls,[])
                for width in (360,375,390,414,768,1280):
                    browser.command('POST','/goog/cdp/execute',{'cmd':'Emulation.setDeviceMetricsOverride',
                        'params':{'width':width,'height':1000,'deviceScaleFactor':1,'mobile':False}})
                    geometry = browser.inspect('''const p=document.querySelector('#agent-detail');return {
                        width:innerWidth,overflow:p.scrollWidth>p.clientWidth,
                        checkboxVisible:p.querySelector('input[type=checkbox]').getBoundingClientRect().width>=20,
                        small:[...p.querySelectorAll('button,label')].some(n=>n.getBoundingClientRect().height<44)};''')
                    self.assertEqual(geometry,{'width':width,'overflow':False,'small':False,'checkboxVisible':True})
                    if width in (360,1280):
                        raw = browser.command('GET','/element/'+browser.find('#agent-detail')+'/screenshot')
                        (output/f'execute-{width}.png').write_bytes(base64.b64decode(raw,validate=True))
                browser.click('#agent-detail input'); click('Start this task')
                browser.wait('return document.querySelector("#agent-detail").textContent.includes("Synthetic cited answer")')
                self.assertEqual(proxy.executions,1); self.assertEqual(len(self.calls),2)
                self.assertEqual(browser.inspect('return document.querySelectorAll("#agent-detail img").length'),0)
                click('Read cited source'); browser.wait('return document.querySelector("#agent-source").textContent.includes("SOURCE-MARKER")')
                self.assertIn(job['version_id'],browser.inspect('return document.querySelector("#agent-source a").href'))
                click('Load / refresh tool calls'); browser.wait('return document.querySelectorAll("#agent-audit details").length===1')
                self.assertEqual(data(self.user.request('GET',self.tasks_path()+'/'+first['id']))['state'],'completed')

                second = create('Read again with a deliberately lost response'); proxy.lose_response = True
                browser.click('#agent-detail input'); click('Start this task')
                browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("Outcome may be unknown")')
                self.assertEqual(proxy.executions,2); self.assertEqual(len(self.calls),4)
                self.assertEqual(data(self.user.request('GET',self.tasks_path()+'/'+second['id']))['state'],'completed')
                click('Refresh task'); browser.wait('return document.querySelector("#agent-detail").textContent.includes("Synthetic cited answer")')
                self.assertEqual(proxy.executions,2)

                third = create('Cancel this synthetic task')
                browser.click('#agent-detail input'); click('Start this task'); self.assertTrue(entered.wait(20))
                click('Refresh task'); browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("running")')
                click('Cancel task'); browser.wait('return document.querySelector("#agent-detail-status").textContent.includes("cancelled")')
                release.set()
                self.assertEqual(data(self.user.request('GET',self.tasks_path()+'/'+third['id']))['state'],'cancelled')
                self.assertEqual(proxy.executions,3); self.assertEqual(len(self.calls),5)
                browser.command('POST','/refresh',{})
                browser.wait('return document.querySelector("#agent-list").textContent.includes("cancelled")')
                self.assertEqual(proxy.executions,3)
                print('PASS real browser: explicit pending execution, private cited result and source, lost response read-only recovery, in-flight cancel, refresh does not execute; six viewports and 44px controls; five synthetic provider responses, zero paid requests.',flush=True)
            finally: release.set(); browser.stop()


if __name__ == '__main__':
    suite = unittest.TestSuite([ExecuteBrowser('test_browser_explicit_execution_lost_response_and_cancel')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
