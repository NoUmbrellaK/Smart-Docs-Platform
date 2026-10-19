"""Real HTTP -> stdio MCP -> C++/isolated SQL; only provider network is replaced."""
import asyncio
from contextlib import contextmanager
import http.client
import json
import tempfile
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from m1_http_test import data
from m3_handover_context_test import HandoverContextE2E
from smartdocs import agent_cli, agent_http, deepseek, worker


def tool(name, arguments, sequence=1):
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
        'tool_calls': [{'id': 'step'+str(sequence), 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments)}}]}}]}


class AgentHttpE2E(HandoverContextE2E):
    @contextmanager
    def gateway(self, respond):
        self.calls, self.model_errors = [], []
        async def transport(request):
            self.assertEqual(str(request.url), deepseek.URL)
            self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-http-key')
            body = json.loads(request.content); self.calls.append(body)
            try:
                answer = await respond(body)
            except Exception as error:
                self.model_errors.append(type(error).__name__+': '+str(error))
                raise
            answer['usage'] = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps(answer).encode()))
        def model(key, audit, **kwargs):
            return deepseek.DeepSeekModel(key, audit, transport=httpx.MockTransport(transport), **kwargs)
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            self.runtime, self.audit = Path(directory)/'runtime', Path(directory)/'audit'
            self.runtime.mkdir(mode=0o700); self.audit.mkdir(mode=0o700)
            server = agent_http.AgentHTTPServer(0, self.server.port, f'http://127.0.0.1:{self.server.port}',
                self.runtime, self.audit, 'synthetic-http-key')
            thread = threading.Thread(target=server.serve_forever); thread.start()
            self.gateway_port = server.server_port
            try:
                with patch.object(agent_cli, 'DeepSeekModel', side_effect=model): yield
            finally:
                server.shutdown(); server.server_close(); thread.join(5)
            self.assertEqual(list(self.runtime.iterdir()), [])
            for trace in self.audit.glob('*.jsonl'):
                self.assertEqual(trace.stat().st_mode & 0o777, 0o600)
                text = trace.read_text()
                for forbidden in ('synthetic-http-key', 'QUESTION-MARKER', 'SOURCE-MARKER', 'CONTEXT-MARKER',
                                  self.user._cookie_header().partition('=')[2]):
                    self.assertNotIn(forbidden, text)

    def execute(self, task, actor=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.gateway_port, timeout=240)
        try:
            connection.request('POST', self.tasks_path()+'/'+task['id']+'/execute', '{}', {
                'Host': f'127.0.0.1:{self.server.port}', 'Origin': f'http://127.0.0.1:{self.server.port}',
                'Cookie': (actor or self.user)._cookie_header(), 'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally: connection.close()

    def test_http_dynamic_search_private_owner_and_revocation(self):
        job = self.upload('recovery.txt', b'SOURCE-MARKER restore the backup.', 'text/plain')
        self.policy(job, True); self.assertEqual(worker.run_once(self.client), 'completed')
        _, reader = self.member('reader'); task = self.create_task(job, reader)
        async def respond(body):
            messages = body['messages']
            if len(self.calls) == 1:
                # MySQL ngram matches shared bigrams, not an exact substring; use disjoint tokens.
                return tool('search_documents', {'mode': 'keyword', 'query': 'zzzzzz'})
            found = json.loads(messages[-1]['content'])['data']
            if len(self.calls) == 2:
                self.assertEqual(found['items'], [])
                return tool('search_documents', {'mode': 'keyword', 'query': 'backup'}, 2)
            self.assertEqual(len(self.calls), 3)
            self.assertEqual([item['file_id'] for item in found['items']], [job['file_id']])
            chunk = found['items'][0]['chunk']
            return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({
                'answer': 'Restore the backup.', 'citations': [{k:chunk[k] for k in ('file_id','version_id','chunk_id')}]})}}]}
        with self.gateway(respond):
            self.assertEqual(self.execute(task)[0], 404)  # admin cannot run another member's private task
            self.assertEqual(self.calls, [])
            self.assertEqual(self.execute(task, reader), (200, {'data': {'state': 'completed'}}),
                             {'provider_calls': len(self.calls), 'fixture_errors': self.model_errors})
            self.assertEqual(self.execute(task, reader)[0], 409); self.assertEqual(len(self.calls), 3)
            saved = data(reader.request('GET', self.tasks_path()+'/'+task['id']+'/run/result'))
            self.assertEqual(saved['model_calls'], 3)
            self.assertEqual(saved['result']['citations'][0]['version_id'], job['version_id'])
            pending = self.create_task(job, reader); self.policy(job, False)
            outcome = self.execute(pending, reader)
            self.assertEqual(outcome[0], 200)
            self.assertEqual(outcome[1]['data']['state'], 'failed')
            self.assertEqual(len(self.calls), 3)  # scope denial happens before provider network
            for trace in self.audit.glob('*.jsonl'):
                self.assertNotIn(reader._cookie_header().partition('=')[2], trace.read_text())

    def test_http_running_duplicate_and_cancellation_stop_next_step(self):
        task = self.create_task(self.approved_file()); entered, release = threading.Event(), threading.Event()
        async def respond(body):
            entered.set()
            if not await asyncio.to_thread(release.wait, 20): raise AssertionError('test release timeout')
            return tool('search_documents', {'mode': 'metadata', 'query': ''})
        with self.gateway(respond):
            results = []
            client = threading.Thread(target=lambda: results.append(self.execute(task))); client.start()
            try:
                self.assertTrue(entered.wait(40))
                self.assertEqual(self.execute(task)[0], 409)
                data(self.user.request('POST', self.tasks_path()+'/'+task['id']+'/cancel'))
            finally:
                release.set(); client.join(45)
            self.assertFalse(client.is_alive())
            self.assertEqual(results[0][0], 200); self.assertEqual(results[0][1]['data']['state'], 'cancelled')
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.count("SELECT COUNT(*) FROM agent_runs WHERE task_id='"+task['id']+"'"), 1)

    def test_http_trusted_report_and_archive_contexts_stop_before_confirmation(self):
        job = self.approved_file(); self.checklist(); report_task, report_path = self.task(job)
        target = self.directory('Approved archive')
        data(self.user.request('PUT', self.base()+'/archive-policy', self.settings(target)))
        archive_task = data(self.user.request('POST', self.tasks_path(), {
            'question': 'Propose only; do not move files', 'confirm_remote_ai': True,
            'versions': [{k:job[k] for k in ('file_id','version_id')}], 'archive_policy_revision': 1}), 201)
        async def respond(body):
            contexts = [json.loads(m['content']) for m in body['messages'] if m['role']=='user' and m['content'].startswith('{')]
            context = next((c['archive_context'] for c in contexts if 'archive_context' in c), None)
            names = {t['function']['name'] for t in body['tools']}
            self.assertNotIn('execute_archive_plan', names)
            if context:
                return tool('create_archive_plan', {'expected_revision': 0, 'policy_revision': context['revision'],
                    'items': [{**context['sources'][0], 'target_directory_id': context['targets'][0]['directory_id'],
                               'reason': 'Unparsed source; human review required.'}]})
            operation = next(c['report_operation_id'] for c in contexts if 'report_operation_id' in c)
            checklist = next(c['handover_checklist'] for c in contexts if 'handover_checklist' in c)
            self.assertEqual(checklist['revision'], 1)
            return tool('save_handover_report', {'operation_id': operation, 'report': {
                'summary': 'Insufficient evidence', 'limitations': 'Source not parsed or read.',
                'recommendations': 'Review manually.', 'items': [{'key': item['key'], 'status': 'uncertain',
                    'notes': 'No content read; cannot conclude completeness.', 'citations': []} for item in checklist['items']]}})
        with self.gateway(respond):
            self.assertEqual(self.execute(report_task), (200, {'data': {'state': 'completed'}}))
            report = data(self.user.request('GET', report_path+'/handover-report'))
            self.assertEqual(report['report']['items'][0]['status'], 'uncertain')
            self.assertEqual(self.execute(archive_task), (200, {'data': {'state': 'waiting_confirmation'}}))
            plan_path = self.tasks_path()+'/'+archive_task['id']+'/archive-plan'
            preview = data(self.user.request('GET', plan_path))
            self.assertFalse(preview['executable']); self.assertEqual(preview['state'], 'preview')
            self.assertEqual(self.file(job)['directory_id'], self.project['root_directory_id'])
            self.assertEqual(self.execute(archive_task)[0], 409); self.assertEqual(len(self.calls), 2)


if __name__ == '__main__':
    suite = unittest.TestSuite(AgentHttpE2E(name) for name in dir(AgentHttpE2E) if name.startswith('test_http_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
