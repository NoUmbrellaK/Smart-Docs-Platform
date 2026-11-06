"""Collector boundaries; synthetic transport is not a business benchmark."""
import copy
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


E2E = Path(__file__).resolve().parents[1] / 'e2e'
sys.path.insert(0, str(E2E))
from benchmark_review import prepare, read_private

PID, TID = 'a' * 32, 'b' * 32
PATH = f'/api/v1/projects/{PID}/agent-tasks/{TID}'
REF = {'file_id': 'c' * 32, 'version_id': 'd' * 32, 'chunk_id': 'e' * 64}
SOURCE = f"/api/v1/projects/{PID}/files/{REF['file_id']}/versions/{REF['version_id']}/chunks/{REF['chunk_id']}"
META = dict(code_revision='unit', recorded_at='unit', environment='synthetic',
            model='none', evidence_path='unit')


class Reply:
    def __init__(self, value, status=200):
        self.status = status
        self.body = json.dumps({'data': value}).encode()


class Client:
    def __init__(self):
        self.calls = []
        self.values = {
            PATH: {'id': TID, 'project_id': PID, 'state': 'completed', 'versions': []},
            PATH + '/run/result': {'state': 'completed', 'result': {
                'answer': 'Fixture claim', 'citations': [REF, REF]}, 'model_calls': 1},
            PATH + '/tool-calls': {'kind': 'backend_requests', 'capacity': 64, 'items': []},
            SOURCE: {'file_id': REF['file_id'], 'version_id': REF['version_id'],
                     'chunk': {**REF, 'text': 'Original text', 'line_start': 1, 'line_end': 1}}}
        self.fail = None
        self.change = False

    def request(self, method, path):
        assert method == 'GET'
        self.calls.append(path)
        if self.fail == path:
            return Reply({'message': 'SECRET ERROR BODY'}, 409)
        if self.fail == 'transport':
            raise OSError('SECRET TRANSPORT DETAIL')
        if self.fail == 'incomplete-body':
            raise http.client.IncompleteRead(b'SECRET RESPONSE BODY')
        value = copy.deepcopy(self.values[path])
        if self.change and path == PATH and self.calls.count(PATH) > 1:
            value['state'] = 'cancelled'
        return Reply(value)


class BenchmarkCaptureTest(unittest.TestCase):
    def setUp(self):
        script = E2E / 'benchmark_capture.py'
        self.assertTrue(script.is_file(), 'read-only result collector not implemented')
        spec = importlib.util.spec_from_file_location('benchmark_capture', script)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.client = Client()
        self.prepared = dict(actor=self.client, task=self.client.values[PATH], path=PATH,
                             admission_http=201, mapping={'fixture@1': {'file_id': REF['file_id']}},
                             body={'question': 'not serialized'}, cookie='SECRET COOKIE')

    def capture(self):
        return self.tool.capture('Q01', self.prepared, META, run_kind='transport_fixture')

    def test_preserves_result_and_sources_without_credentials_or_auto_grading(self):
        result = self.capture()
        self.assertTrue(result['capture']['complete'])
        self.assertEqual(result['run_result'], self.client.values[PATH + '/run/result'])
        self.assertEqual(result['citation_sources'][0]['source']['chunk']['text'], 'Original text')
        self.assertEqual(result['fixture_mapping'], self.prepared['mapping'])
        self.assertEqual(len(result['citation_sources']), 1)
        self.assertEqual(len(prepare(result)['citations']), 2)  # occurrence denominator unchanged
        self.assertIsNone(prepare(result)['business']['verdict'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('question', result)
        self.assertEqual(set(self.client.calls), set(self.client.values))

    def test_failure_and_changed_reads_cannot_be_reviewed_as_complete(self):
        for failure in (PATH + '/run/result', 'transport', 'incomplete-body', 'changed', 'wrong-task'):
            self.client = Client()
            self.prepared['actor'] = self.client
            self.client.fail = failure
            self.client.change = failure == 'changed'
            if failure == 'wrong-task':
                self.client.values[PATH]['id'] = 'f' * 32
            with self.subTest(failure=failure):
                result = self.capture()
                self.assertFalse(result['capture']['complete'])
                self.assertNotIn('SECRET', json.dumps(result))
                with self.assertRaises(ValueError):
                    prepare(result)

    def test_rejected_admission_does_not_issue_requests_or_create_a_run(self):
        self.prepared.update(task=None, path=None, admission_http=404)
        result = self.tool.capture('Q08', self.prepared, META, run_kind='admission_rejection')
        self.assertTrue(result['capture']['complete'])
        self.assertIsNone(result['run_result'])
        self.assertEqual(self.client.calls, [])
        self.assertEqual(prepare(result)['citations'], [])

    def test_path_validation_does_not_follow_supplied_urls(self):
        for path in ('https://example.invalid/task', PATH + '?cookie=secret', PATH + '/../../'):
            self.prepared['path'] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.capture()
        self.assertEqual(self.client.calls, [])

    def test_committed_receipt_mismatch_marks_capture_incomplete(self):
        self.client.values[PATH]['archive_policy_revision'] = 1
        receipt = {'status': 'executed', 'operation_id': 'f' * 32, 'moved_count': 1}
        self.client.values[PATH + '/run/result'] = {
            'state': 'completed', 'kind': 'archive_operation', 'result': None,
            'archive_operation': receipt}
        self.client.values[PATH + '/archive-plan'] = {'task_id': TID, 'project_id': PID}
        self.client.values[PATH + '/archive-plan/result'] = copy.deepcopy(receipt)
        self.assertTrue(self.capture()['capture']['complete'])
        self.client.values[PATH + '/archive-plan/result']['moved_count'] = 2
        result = self.capture()
        self.assertFalse(result['capture']['complete'])
        with self.assertRaises(ValueError):
            prepare(result)

    def archive_failure(self, state='failed', kind=None):
        self.client.values[PATH].update(state=state, archive_policy_revision=1)
        self.client.values[PATH + '/run/result'] = {
            'state': state, 'kind': kind, 'result': None, 'error_code': 'scope_changed'}

    def test_failed_archive_missing_plan_is_observed_twice_without_auto_pass(self):
        self.archive_failure()
        request = self.client.request
        replies = []

        def missing(method, path):
            if path == PATH + '/archive-plan':
                replies.append(path)
                return Reply({'message': 'SECRET ERROR BODY'}, 404)
            return request(method, path)

        with patch.object(self.client, 'request', side_effect=missing):
            result = self.capture()
        self.assertTrue(result['capture']['complete'], result['capture'])
        self.assertEqual(len(replies), 2)
        self.assertIsNone(result['archive_plan'])
        self.assertEqual(result['run_result']['error_code'], 'scope_changed')
        self.assertIsNone(result['run_result']['result'])
        self.assertIsNone(prepare(result)['business']['verdict'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_missing_plan_exception_is_narrow_and_rechecked(self):
        scenarios = [
            ('pending', None, (404,)), ('running', None, (404,)),
            ('waiting_confirmation', None, (404,)), ('waiting_execution', None, (404,)),
            ('completed', 'archive_operation', (404,)),
            ('failed', 'archive_operation', (404,)),
            ('failed', None, (403,)), ('failed', None, (409,)),
            ('failed', None, (503,)), ('failed', None, (404, 200)),
            ('failed', None, (200, 404)), ('failed', None, (404, 403)),
        ]
        for state, kind, statuses in scenarios:
            with self.subTest(state=state, kind=kind, statuses=statuses):
                self.archive_failure(state, kind)
                request = self.client.request
                remaining = iter(statuses)

                def changing(method, path):
                    if path == PATH + '/archive-plan':
                        return Reply({'task_id': TID, 'project_id': PID}, next(remaining))
                    return request(method, path)

                with patch.object(self.client, 'request', side_effect=changing):
                    result = self.capture()
                self.assertFalse(result['capture']['complete'])
                with self.assertRaises(ValueError):
                    prepare(result)

    def test_failed_archive_existing_plan_is_retained(self):
        self.archive_failure()
        plan = {'task_id': TID, 'project_id': PID, 'state': 'proposed'}
        self.client.values[PATH + '/archive-plan'] = plan
        result = self.capture()
        self.assertTrue(result['capture']['complete'])
        self.assertEqual(result['archive_plan'], plan)

    def test_private_exclusive_output_and_size_bound(self):
        result = self.capture()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'snapshot.json'
            self.tool.write_snapshot(output, result)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_private(output), result)
            with self.assertRaises(FileExistsError):
                self.tool.write_snapshot(output, result)
            link = Path(directory) / 'link'
            link.symlink_to(output)
            with self.assertRaises(FileExistsError):
                self.tool.write_snapshot(link, result)
            with self.assertRaises(ValueError):
                self.tool.write_snapshot(Path(directory) / 'large', {'x': 'x' * (2 * 1024 * 1024)})


if __name__ == '__main__':
    unittest.main(verbosity=2)
