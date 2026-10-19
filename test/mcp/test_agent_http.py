"""HTTP admission and private context lifecycle; provider/runner are replaced only here."""
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

PROJECT, TASK = 'a'*32, 'c'*32
ORIGIN = 'https://docs.example.test'
TOKEN = 'b'*64
PATH = f'/api/v1/projects/{PROJECT}/agent-tasks/{TASK}/execute'


@contextlib.contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield server.server_port
    finally:
        server.shutdown(); server.server_close(); thread.join()


class AgentHttpTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('smartdocs.agent_http'), 'controlled Agent HTTP entry missing')
        self.module = importlib.import_module('smartdocs.agent_http')
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.runtime, self.audit = self.root/'runtime', self.root/'audit'
        self.runtime.mkdir(mode=0o700); self.audit.mkdir(mode=0o700)
        self.reads, self.runs, self.statuses = [], [], [200]
        self.state, self.bad_body, self.run_error = 'pending', None, False
        self.entered, self.release = threading.Event(), threading.Event()
        self.block_run = False
        owner = self
        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                owner.reads.append((self.path, dict(self.headers)))
                status = owner.statuses[min(len(owner.reads)-1, len(owner.statuses)-1)]
                body = owner.bad_body if owner.bad_body is not None else json.dumps({'data': {
                    'id': TASK, 'project_id': PROJECT, 'state': owner.state, 'versions': [], 'secret': 'NOT-FOR-HTTP'}}).encode()
                self.send_response(status); self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body)
        self.backend_port = self.stack.enter_context(running(HTTPServer(('127.0.0.1', 0), Backend)))
        async def execute(context_path, audit_path, key, max_calls, *, encoder_socket=None):
            from smartdocs.mcp_read import load_context
            context = load_context(context_path)
            self.assertEqual(context, {'backend_port': self.backend_port, 'origin': ORIGIN,
                'session_token': TOKEN, 'project_id': PROJECT, 'task_id': TASK, 'scope': 'task_versions'})
            self.assertEqual(Path(context_path).parent.parent, self.runtime)
            self.assertEqual(Path(audit_path).parent, self.audit)
            self.assertEqual(key, 'synthetic-key'); self.assertEqual(max_calls, 8)
            self.assertEqual(encoder_socket, '/private/encoder.sock')
            self.runs.append(context)
            if self.block_run:
                self.entered.set()
                if not self.release.wait(10): raise RuntimeError('test release timed out')
            if self.run_error: raise self.run_error
            self.state = 'completed'
            return {'state': 'completed', 'result': {'answer': 'NEVER-EXPOSE-CONTENT'}}
        self.stack.enter_context(patch.object(self.module, 'run_context', execute))
        self.server = self.module.AgentHTTPServer(0, self.backend_port, ORIGIN,
            self.runtime, self.audit, 'synthetic-key', encoder_socket='/private/encoder.sock')
        self.port = self.stack.enter_context(running(self.server))

    def request(self, body=b'{}', *, headers=None, path=PATH, method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        values = {'Host': 'docs.example.test', 'Origin': ORIGIN, 'Cookie': 'smartdocs_session='+TOKEN,
                  'Content-Type': 'application/json'}; values.update(headers or {})
        try:
            conn.request(method, path, body, values); response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally: conn.close()

    def test_bound_context_is_private_cleaned_and_only_status_is_returned(self):
        status, headers, raw = self.request(headers={'Cookie': 'other=secret; smartdocs_session='+TOKEN,
            'Authorization': 'Bearer not-forwarded', 'X-Forwarded-Host': 'evil.test'})
        self.assertEqual(status, 200); self.assertEqual(json.loads(raw), {'data': {'state': 'completed'}})
        self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertEqual(headers['Connection'], 'close')
        self.assertEqual(len(self.runs), 1); self.assertEqual(len(self.reads), 2)
        for path, forwarded in self.reads:
            self.assertEqual(path, PATH.removesuffix('/execute'))
            self.assertEqual(forwarded['Cookie'], 'smartdocs_session='+TOKEN)
            self.assertEqual(forwarded['Host'], 'docs.example.test'); self.assertEqual(forwarded['Origin'], ORIGIN)
            self.assertNotIn('Authorization', forwarded); self.assertNotIn('X-Forwarded-Host', forwarded)
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_origin_cookie_and_framing_fail_before_any_backend_or_execution(self):
        for values, status in [({'Origin': 'https://evil.test'}, 403), ({'Host': 'evil.test'}, 403),
            ({'Cookie': ''}, 401), ({'Cookie': 'smartdocs_session='+TOKEN+'; smartdocs_session='+TOKEN}, 401)]:
            self.assertEqual(self.request(headers=values)[0], status)
        for extra in ['Origin: https://evil.test\r\n', 'Content-Length: 2\r\n', 'Transfer-Encoding: chunked\r\n']:
            with socket.create_connection(('127.0.0.1', self.port), timeout=5) as client:
                client.sendall((f'POST {PATH} HTTP/1.1\r\nHost: docs.example.test\r\nOrigin: {ORIGIN}\r\n'
                    f'Cookie: smartdocs_session={TOKEN}\r\nContent-Type: application/json\r\nContent-Length: 2\r\n{extra}\r\n{{}}').encode())
                self.assertIn(b' 400 ', client.recv(4096).split(b'\r\n')[0])
        self.assertEqual(self.reads, []); self.assertEqual(self.runs, [])

    def test_body_cannot_supply_identity_model_budget_or_approval(self):
        for body in [b'[]', b'null', b'{"approved":true}', b'{"session_token":"fake"}', b'{"max_calls":1}',
                     b'{"api_key":"fake"}', b'{"x":1,"x":2}', b'{"x":NaN}', b'']:
            self.assertEqual(self.request(body)[0], 400)
        self.assertEqual(self.request(b'x'*4097)[0], 413)
        self.assertEqual(self.request(headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request(path=PATH+'?resume=true')[0], 404)
        self.assertEqual(self.request(method='GET')[0], 501)
        self.assertEqual(self.reads, []); self.assertEqual(self.runs, [])

    def test_denied_nonpending_or_malformed_task_never_runs(self):
        for status in [401, 403, 404, 429, 302, 500]:
            self.statuses = [status]
            code, _, raw = self.request(); self.assertEqual(code, status if status in (401,403,404,429) else 503)
            self.assertNotIn(b'NOT-FOR-HTTP', raw)
        self.statuses = [200]
        for state in ['running', 'completed', 'cancelled', 'waiting_execution', 'interrupted', 'failed']:
            self.state = state; self.assertEqual(self.request()[0], 409)
        self.state = 'pending'
        for raw in [b'internals', b'x'*65537, b'{"data":{"id":"wrong"}}']:
            self.bad_body = raw; self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.runs, []); self.assertEqual(list(self.runtime.iterdir()), [])

    def test_execution_error_is_sanitized_and_private_context_is_removed(self):
        for error in (RuntimeError('NEVER-EXPOSE-SECRET'), ValueError('NEVER-EXPOSE-SECRET')):
            self.run_error = error
            status, _, raw = self.request(); self.assertEqual(status, 503)
            self.assertNotIn(b'NEVER-EXPOSE', raw); self.assertNotIn(TOKEN.encode(), raw)
            self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertEqual(len(self.runs), 2)

    def test_access_is_rechecked_after_execution_before_reporting_outcome(self):
        self.statuses = [200, 403]
        status, _, raw = self.request(); self.assertEqual(status, 403)
        self.assertNotIn(b'completed', raw); self.assertEqual(len(self.runs), 1)

    def test_third_connection_is_busy_without_new_execution(self):
        with contextlib.ExitStack() as stack:
            for _ in range(2):
                client = stack.enter_context(socket.create_connection(('127.0.0.1', self.port), timeout=5))
                client.sendall(b'POST ')
            status, _, raw = self.request()
            self.assertEqual(status, 503); self.assertEqual(json.loads(raw)['error']['code'], 'agent_busy')
        self.assertEqual(self.runs, [])

    def test_insecure_state_directories_or_empty_key_rejected_before_listening(self):
        bad = self.root/'bad'; bad.mkdir(); bad.chmod(0o755)
        link = self.root/'link'; link.symlink_to(self.runtime)
        for runtime, audit, key in [(bad,self.audit,'key'), (link,self.audit,'key'),
                                    (self.runtime,bad,'key'), (self.runtime,self.audit,'')]:
            with self.assertRaises(ValueError):
                self.module.AgentHTTPServer(0, self.backend_port, ORIGIN, runtime, audit, key)

    def test_server_close_waits_for_admitted_execution_and_context_cleanup(self):
        self.block_run = True
        result, closed = [], threading.Event()
        client = threading.Thread(target=lambda: result.append(self.request()))
        client.start()
        closer = None
        try:
            self.assertTrue(self.entered.wait(3))
            self.assertEqual(len(list(self.runtime.glob('task-*/context.json'))), 1)
            self.server.shutdown()
            def close():
                self.server.server_close(); closed.set()
            closer = threading.Thread(target=close); closer.start()
            self.assertFalse(closed.wait(0.1), 'close must drain admitted handlers')
        finally:
            self.release.set(); client.join(5)
            if closer: closer.join(5)
        self.assertTrue(closed.is_set()); self.assertEqual(result[0][0], 200)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_service_sigterm_exits_cleanly_without_logging_credentials(self):
        self.assertNotEqual(os.geteuid(), 0, 'run this suite as an unprivileged account')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
        env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': os.environ['PYTHONPATH'],
               'DEEPSEEK_API_KEY': 'synthetic-key', 'SMARTDOCS_PUBLIC_ORIGIN': ORIGIN}
        process = subprocess.Popen([sys.executable, '-B', '-m', 'smartdocs.agent_http',
            '--port', str(port), '--backend-port', str(self.backend_port),
            '--runtime-dir', str(self.runtime), '--audit-dir', str(self.audit)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+10
            while True:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1): break
                except OSError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        self.fail('service did not start')
                    time.sleep(.05)
            process.terminate()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(stdout+stderr, b'')
            self.assertEqual(list(self.runtime.iterdir()), [])
        finally:
            if process.poll() is None: process.kill()
            process.communicate()


if __name__ == '__main__': unittest.main()
