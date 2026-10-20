"""Fixed inputs -> real Agent/stdio/SQL -> capture; provider network alone is fake."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from benchmark_execute import execute
from benchmark_review import prepare, read_private, summarize
from m1_http_test import data
from m3_agent_http_test import tool
from m3_fixed_inputs_test import FixedInputsE2E, TASKS
from smartdocs import agent_cli, deepseek


class ExecuteGuards(unittest.TestCase):
    def test_mismatched_backend_is_rejected_before_http_or_artifacts(self):
        case = TASKS[0]
        task = {'id': 'a' * 32, 'project_id': 'b' * 32}
        prepared = {'body': {'question': case['input']}, 'admission_http': 201,
                    'actor': SimpleNamespace(port=10001), 'task': task,
                    'path': '/api/v1/projects/' + task['project_id'] + '/agent-tasks/' + task['id']}
        metadata = dict.fromkeys(('code_revision', 'recorded_at', 'environment', 'model', 'evidence_path'), 'test')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'wrong-port'
            with self.assertRaises(ValueError):
                asyncio.run(execute(case['id'], prepared, metadata, output, backend_port=10002,
                    api_key='', max_calls=1, run_kind='transport_fixture'))
            self.assertFalse(output.exists())


class ExecuteE2E(FixedInputsE2E):
    def setup_case(self, case_id):
        self.case_id = case_id
        return self.prepare_case(next(case for case in TASKS if case['id'] == case_id))

    @contextmanager
    def provider(self, respond):
        self.requests = []

        async def transport(request):
            self.assertEqual(str(request.url), deepseek.URL)
            self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-execute-key')
            body = json.loads(request.content)
            self.requests.append(body)
            reply = respond(body)
            if isinstance(reply, httpx.Response):
                return reply
            reply['usage'] = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps(reply).encode()))

        def model(key, audit, **kwargs):
            return deepseek.DeepSeekModel(key, audit, transport=httpx.MockTransport(transport), **kwargs)

        with patch.object(agent_cli, 'DeepSeekModel', side_effect=model):
            yield

    def invoke(self, prepared, directory, **changes):
        options = dict(backend_port=self.server.port, api_key='synthetic-execute-key',
                       max_calls=3, run_kind='transport_fixture')
        options.update(changes)
        return asyncio.run(execute(self.case_id, prepared, {
            'code_revision': 'test-checkout', 'recorded_at': 'contract-test',
            'environment': 'isolated HTTP/SQL fixture', 'model': 'transport fixture',
            'evidence_path': 'm5_benchmark_execute_test.py'}, directory, **options))

    def check_artifacts(self, directory, snapshot, prepared):
        self.assertEqual(read_private(directory / 'snapshot.json'), snapshot)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertFalse((directory / 'context.json').exists())
        files = list(directory.iterdir())
        self.assertEqual({p.name for p in files}, {'audit.jsonl', 'snapshot.json'})
        for path in files:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            contents = path.read_text()
            self.assertNotIn('synthetic-execute-key', contents)
            for cookie in prepared['actor'].cookies:
                self.assertNotIn(cookie.value, contents)
        trace = [json.loads(line) for line in (directory / 'audit.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e['event'] == 'model_attempt' for e in trace), len(self.requests))
        for request in self.requests:
            text = json.dumps(request)
            self.assertNotIn('"expected"', text)
            self.assertNotIn('"fixture_mapping"', text)

    def test_query_runs_real_agent_and_captures_original_before_cleanup(self):
        prepared = self.setup_case('Q01')

        def respond(body):
            if len(self.requests) == 1:
                return tool('search_documents', {'mode': 'keyword', 'query': 'materials'})
            found = json.loads(body['messages'][-1]['content'])['data']['items']
            self.assertEqual(len(found), 1)
            ref = {k: found[0]['chunk'][k] for k in ('file_id', 'version_id', 'chunk_id')}
            return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                'content': json.dumps({'answer': 'Protocol fixture only.', 'citations': [ref]})}}]}

        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(respond):
            output = Path(folder) / 'query'
            snapshot = self.invoke(prepared, output)
            self.check_artifacts(output, snapshot, prepared)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(snapshot['execution']['outcome'], {'state': 'completed'})
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertEqual(snapshot['run_result']['model_calls'], 2)
        self.assertEqual(len(snapshot['citation_sources']), 1)
        self.assertIn('/api/v2/materials', snapshot['citation_sources'][0]['source']['chunk']['text'])
        summary = summarize(snapshot, prepare(snapshot))
        self.assertIsNone(summary['business_verdict'])
        self.assertFalse(summary['execution_verified'])

    def test_report_uses_backend_checklist_without_oracle_and_is_captured(self):
        prepared = self.setup_case('H01')

        def respond(body):
            contexts = [json.loads(m['content']) for m in body['messages']
                        if m['role'] == 'user' and m['content'].startswith('{')]
            operation = next(c['report_operation_id'] for c in contexts if 'report_operation_id' in c)
            checklist = next(c['handover_checklist'] for c in contexts if 'handover_checklist' in c)
            return tool('save_handover_report', {'operation_id': operation, 'report': {
                'summary': 'Protocol fixture', 'limitations': 'No semantic evaluation',
                'recommendations': 'Review manually', 'items': [
                    {'key': item['key'], 'status': 'uncertain', 'notes': 'Not read', 'citations': []}
                    for item in checklist['items']]}})

        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(respond):
            output = Path(folder) / 'report'
            snapshot = self.invoke(prepared, output)
            self.check_artifacts(output, snapshot, prepared)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertEqual(snapshot['run_result']['kind'], 'handover_report')
        self.assertEqual(snapshot['handover_report']['report']['summary'], 'Protocol fixture')

    def test_archive_stays_unconfirmed_and_keeps_fixture_alive(self):
        prepared = self.setup_case('A06')
        before = data(prepared['actor'].request('GET', self.base() + '/files'))

        def respond(body):
            contexts = [json.loads(m['content']) for m in body['messages']
                        if m['role'] == 'user' and m['content'].startswith('{')]
            context = next(c['archive_context'] for c in contexts if 'archive_context' in c)
            return tool('create_archive_plan', {'expected_revision': 0, 'policy_revision': context['revision'],
                'items': [{**source, 'target_directory_id': None, 'reason': 'Needs human review'}
                          for source in context['sources']]})

        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(respond):
            output = Path(folder) / 'archive'
            snapshot = self.invoke(prepared, output)
            self.check_artifacts(output, snapshot, prepared)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(snapshot['execution']['outcome'], {'state': 'waiting_confirmation'})
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertIsNone(snapshot['archive_plan']['decision'])
        self.assertEqual(self.count('SELECT COUNT(*) FROM archive_decisions'), 0)
        self.assertEqual(data(prepared['actor'].request('GET', self.base() + '/files')), before)
        self.assertEqual(data(prepared['actor'].request('GET', prepared['path']))['state'],
                         'waiting_confirmation')

    def test_rejected_admission_never_starts_agent_or_needs_key(self):
        prepared = self.setup_case('Q08')
        before = self.count('SELECT COUNT(*) FROM agent_runs')
        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(
                lambda body: self.fail('admission rejection must never reach provider')):
            output = Path(folder) / 'rejected'
            snapshot = self.invoke(prepared, output, api_key='', run_kind='admission_rejection')
            self.assertEqual({p.name for p in output.iterdir()}, {'snapshot.json'})
            self.assertEqual(read_private(output / 'snapshot.json'), snapshot)
        self.assertEqual(self.requests, [])
        self.assertTrue(snapshot['capture']['complete'])
        self.assertEqual(self.count('SELECT COUNT(*) FROM agent_runs'), before)

    def test_provider_failure_is_retained_without_retry(self):
        prepared = self.setup_case('Q01')
        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(
                lambda body: httpx.Response(503)):
            output = Path(folder) / 'failed'
            snapshot = self.invoke(prepared, output)
            self.check_artifacts(output, snapshot, prepared)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(snapshot['execution']['outcome'],
                         {'state': 'failed', 'error_code': 'provider_unavailable'})
        self.assertEqual(snapshot['run_result']['state'], 'failed')
        self.assertTrue(snapshot['capture']['complete'])

    def test_budget_cannot_be_silently_expanded(self):
        prepared = self.setup_case('Q01')
        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(
                lambda body: tool('search_documents', {'mode': 'metadata', 'query': ''})):
            output = Path(folder) / 'budget'
            snapshot = self.invoke(prepared, output, max_calls=1)
            self.check_artifacts(output, snapshot, prepared)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(snapshot['execution']['outcome'],
                         {'state': 'failed', 'error_code': 'budget_exhausted'})

    def test_existing_destination_or_bad_case_fails_before_claim(self):
        prepared = self.setup_case('Q01')
        before = self.count('SELECT COUNT(*) FROM agent_runs')
        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(
                lambda body: self.fail('invalid setup must never reach provider')):
            output = Path(folder) / 'existing'
            output.mkdir()
            with self.assertRaises(FileExistsError):
                self.invoke(prepared, output)
            self.case_id = 'Q02'
            with self.assertRaises(ValueError):
                self.invoke(prepared, Path(folder) / 'wrong-case')
            self.assertFalse((Path(folder) / 'wrong-case').exists())
        self.assertEqual(self.requests, [])
        self.assertEqual(self.count('SELECT COUNT(*) FROM agent_runs'), before)
        self.assertEqual(data(prepared['actor'].request('GET', prepared['path']))['state'], 'pending')


if __name__ == '__main__':
    suite = unittest.TestSuite(ExecuteE2E(name) for name in ExecuteE2E.__dict__ if name.startswith('test_'))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ExecuteGuards))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
