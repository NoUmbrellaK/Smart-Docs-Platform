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
from m3_fixed_inputs_test import FixedInputsE2E, TASKS, VERSIONS, pair
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
        self.provider_errors = []

        async def transport(request):
            self.assertEqual(str(request.url), deepseek.URL)
            self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-execute-key')
            body = json.loads(request.content)
            self.requests.append(body)
            try:
                reply = respond(body)
            except Exception as error:
                self.provider_errors.append(error)
                raise
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
        snapshot = asyncio.run(execute(self.case_id, prepared, {
            'code_revision': 'test-checkout', 'recorded_at': 'contract-test',
            'environment': 'isolated HTTP/SQL fixture', 'model': 'transport fixture',
            'evidence_path': 'm5_benchmark_execute_test.py'}, directory, **options))
        if self.provider_errors:
            raise self.provider_errors[0]  # Do not hide receiver assertions behind a runner error code.
        return snapshot

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
            result = next(m for m in reversed(body['messages']) if m['role'] == 'tool')
            found = json.loads(result['content'])['data']['items']
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

    def test_h05_metadata_only_path_preserves_frozen_question_and_dependencies(self):
        # Characterize the real C++/SQL/stdio path, not the scripted model's choices.
        # Lost question/page flags or missing metadata-only source dependencies must fail.
        prepared = self.setup_case('H05')
        question = '本次只检查元数据搜索的第一页（最多10项），不翻页；报告必须说明没有完整核查。'
        self.assertEqual(prepared['body']['question'], question)
        first_page = []

        def respond(body):
            self.assertEqual([m for m in body['messages'] if m['content'] == question],
                             [{'role': 'user', 'content': question}])
            self.assertEqual({t['function']['name'] for t in body['tools']},
                             {'search_documents', 'read_document_version', 'save_handover_report'})
            if len(self.requests) == 1:
                return tool('search_documents', {'mode': 'metadata', 'query': '', 'page': 1, 'page_size': 10})
            results = [json.loads(m['content']) for m in body['messages'] if m['role'] == 'tool']
            self.assertEqual(len(results), 1)
            found = results[0]['data']
            self.assertEqual((found['page'], found['page_size'], found['has_more'],
                              found['next_page'], found['truncated']), (1, 10, True, 2, True))
            self.assertEqual(found['coverage']['current_files'], 11)
            self.assertEqual(len(found['items']), 10)
            self.assertTrue(all('chunk' not in item and 'chunks' not in item for item in found['items']))
            first_page.extend({k: item[k] for k in ('file_id', 'version_id')} for item in found['items'])
            self.assertEqual(first_page, sorted(prepared['body']['versions'], key=lambda p: p['file_id'])[:10])
            contexts = [json.loads(m['content']) for m in body['messages']
                        if m['role'] == 'user' and m['content'].startswith('{')]
            operation = next(c['report_operation_id'] for c in contexts if 'report_operation_id' in c)
            checklist = next(c['handover_checklist'] for c in contexts if 'handover_checklist' in c)
            self.assertEqual(checklist, {k: prepared['checklist'][k] for k in ('revision', 'items')})
            return tool('save_handover_report', {'operation_id': operation, 'report': {
                'summary': 'Offline protocol fixture: incomplete metadata-only inspection.',
                'limitations': 'First page only; no body reads; no semantic evaluation.',
                'recommendations': 'Inspect remaining materials only when requested.',
                'items': [{'key': item['key'], 'status': 'uncertain', 'notes': 'Content not checked',
                           'citations': []} for item in checklist['items']]}}, sequence=2)

        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(respond):
            output = Path(folder) / 'metadata'
            snapshot = self.invoke(prepared, output, max_calls=2)
            self.check_artifacts(output, snapshot, prepared)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(snapshot['execution']['outcome'], {'state': 'completed'})
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertEqual([c['tool'] for c in snapshot['tool_calls']['items']],
                         ['search_documents', 'save_handover_report'])
        report = snapshot['handover_report']
        self.assertEqual(report['state'], 'draft')
        self.assertEqual(report['checklist']['revision'], 1)
        self.assertEqual(len(report['selected_versions']), 11)
        self.assertEqual(report['source_versions'], first_page)
        self.assertEqual({item['key']: item['status'] for item in report['report']['items']},
                         {key: 'uncertain' for key in ('requirements', 'design', 'api', 'test', 'deploy', 'issues')})
        self.assertTrue(all(item['citations'] == [] for item in report['report']['items']))
        self.assertEqual(snapshot['citation_sources'], [])
        self.assertIsNone(summarize(snapshot, prepare(snapshot))['business_verdict'])

    def test_h07_restricted_material_stays_out_of_serialized_requests_and_report(self):
        # Catch scope/approval filtering regressions at the actual provider boundary.
        # Only model responses are scripted; SQL, MCP, serialization and report writes are real.
        prepared = self.setup_case('H07')
        self.assertEqual(len(prepared['loaded']), 8)
        allowed = [pair(prepared['loaded'][ref]) for ref in (
            'requirements@1', 'design@1', 'api@2', 'tests@1', 'deployment@2', 'issues@1')]
        deployment = pair(prepared['loaded']['deployment@2'])
        forbidden = ['INTERNAL-ONLY-MARKER-73', 'BETA-ONLY-MARKER-52']
        for ref in ('internal@1', 'beta_private@1'):
            document, version = VERSIONS[ref]
            forbidden.extend([document['name'], *version['paragraphs']])
        citation = {}

        def respond(body):
            serialized = json.dumps(body, ensure_ascii=False)
            for secret in forbidden:
                self.assertNotIn(secret, serialized)
            self.assertIn({'role': 'user', 'content': '核查允许范围，不要读内部资料或其他项目资料。'},
                          body['messages'])
            if len(self.requests) == 1:
                return tool('search_documents', {'mode': 'metadata', 'query': '', 'page_size': 10})
            results = [json.loads(m['content']) for m in body['messages'] if m['role'] == 'tool']
            metadata = results[0]['data']
            self.assertCountEqual([pair(item) for item in metadata['items']], allowed)
            self.assertEqual(metadata['coverage']['current_files'], 6)
            self.assertFalse(metadata['has_more'])
            if len(self.requests) == 2:
                self.assertEqual(len(results), 1)
                return tool('read_document_version', {**deployment, 'range': {}}, sequence=2)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[1]['status'], 'success')
            chunks = results[1]['data']['chunks']
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0]['text'], '\n\n'.join(VERSIONS['deployment@2'][1]['paragraphs']))
            self.assertIn('ORBIT-27', chunks[0]['text'])  # Positive control: body really reached provider.
            citation.update({**deployment, 'chunk_id': chunks[0]['chunk_id']})
            contexts = [json.loads(m['content']) for m in body['messages']
                        if m['role'] == 'user' and m['content'].startswith('{')]
            operation = next(c['report_operation_id'] for c in contexts if 'report_operation_id' in c)
            checklist = next(c['handover_checklist'] for c in contexts if 'handover_checklist' in c)
            return tool('save_handover_report', {'operation_id': operation, 'report': {
                'summary': 'Offline boundary fixture: only deployment content inspected.',
                'limitations': 'Other five bodies unread; no model semantic evaluation.',
                'recommendations': 'Human review required.',
                'items': [{'key': item['key'],
                           'status': 'found' if item['key'] == 'deploy' else 'uncertain',
                           'notes': 'Deployment requires backup ORBIT-27.' if item['key'] == 'deploy'
                                    else 'Content not inspected.',
                           'citations': [citation] if item['key'] == 'deploy' else []}
                          for item in checklist['items']]}}, sequence=3)

        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(respond):
            output = Path(folder) / 'boundary'
            snapshot = self.invoke(prepared, output, max_calls=3)
            self.check_artifacts(output, snapshot, prepared)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(snapshot['execution']['outcome'], {'state': 'completed'})
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        report = snapshot['handover_report']
        self.assertEqual(report['state'], 'draft')
        self.assertCountEqual(report['selected_versions'], allowed)
        self.assertCountEqual(report['source_versions'], allowed)
        self.assertEqual(len(snapshot['citation_sources']), 1)
        self.assertEqual(snapshot['citation_sources'][0]['reference'], citation)
        for secret in forbidden:
            self.assertNotIn(secret, json.dumps(snapshot, ensure_ascii=False))
        for table in ('archive_plans', 'archive_decisions'):
            self.assertEqual(self.count("SELECT COUNT(*) FROM " + table +
                                        " WHERE task_id='" + prepared['task']['id'] + "'"), 0)
        self.assertIsNone(summarize(snapshot, prepare(snapshot))['business_verdict'])

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
