"""Real C++/SQL snapshot collection; scripted outcomes are NOT model evaluation."""
import json
from pathlib import Path
import tempfile
import unittest

from benchmark_capture import capture, write_snapshot
from benchmark_review import prepare, read_private, summarize
from m1_http_test import data
from m3_fixed_inputs_test import FixedInputsE2E, TASKS


class CaptureE2E(FixedInputsE2E):
    def setup_case(self, case_id):
        return self.prepare_case(next(case for case in TASKS if case['id'] == case_id))

    def collect(self, case_id, prepared, run_kind='transport_fixture'):
        snapshot = capture(case_id, prepared, {
            'code_revision': 'test-checkout', 'recorded_at': 'contract-test',
            'environment': 'isolated HTTP/SQL fixture', 'model': 'no model invoked',
            'evidence_path': 'm5_benchmark_capture_test.py'}, run_kind=run_kind)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'snapshot.json'
            write_snapshot(output, snapshot)
            self.assertEqual(read_private(output), snapshot)
        return snapshot

    def read_and_admit(self, prepared, *, report=False):
        actor, path = prepared['actor'], prepared['path']
        started = data(actor.request('POST', path + '/run/start'))
        version = prepared['body']['versions'][0]
        response = actor.request('POST', path + '/tools/read', {**version, 'range': {}})
        chunk = data(response)['chunks'][0]
        check = {'tool_request_ids': [response.json()['request_id']]}
        if report:
            check['handover_checklist_revision'] = prepared['checklist']['revision']
        data(actor.request('POST', path + '/run/check', check))
        return started, {k: chunk[k] for k in ('file_id', 'version_id', 'chunk_id')}

    def test_capture_answer_retains_actual_original(self):
        prepared = self.setup_case('Q01')
        _, ref = self.read_and_admit(prepared)
        actor, path = prepared['actor'], prepared['path']
        outcome = {'status': 'completed',
            'result': {'answer': 'Scripted protocol fixture, not business success.',
                       'citations': [ref, ref]}, 'error_code': None}
        # Ordinary answers forbid duplicate references; report items may share one.
        self.assertEqual(actor.request('POST', path + '/run/finish', outcome).status, 400)
        outcome['result']['citations'] = [ref]
        data(actor.request('POST', path + '/run/finish', outcome))
        before = self.count('SELECT COUNT(*) FROM agent_tool_calls')
        snapshot = self.collect('Q01', prepared)
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertEqual(self.count('SELECT COUNT(*) FROM agent_tool_calls'), before)
        review = prepare(snapshot)
        self.assertEqual(len(review['citations']), 1)
        self.assertEqual(len(snapshot['citation_sources']), 1)
        source = snapshot['citation_sources'][0]['source']
        mapping = next(v for v in prepared['mapping'].values() if v['version_id'] == ref['version_id'])
        self.assertEqual(source['source_sha256'], mapping['sha256'])
        self.assertTrue(source['chunk']['text'])
        summary = summarize(snapshot, review)
        self.assertIsNone(summary['business_verdict'])
        self.assertFalse(summary['execution_verified'])
        print(json.dumps({'collector': 'Q01', 'citations': 1, 'unique_sources': 1,
                          'business_task': 'not_run', 'provider_requests': 0}), flush=True)

    def test_capture_report_and_dependency_revocation(self):
        prepared = self.setup_case('H01')
        started, ref = self.read_and_admit(prepared, report=True)
        actor, path = prepared['actor'], prepared['path']
        report = {'summary': 'Protocol fixture only', 'limitations': 'No semantic evaluation',
                  'recommendations': 'Review manually', 'items': [
            {'key': item['key'], 'status': 'uncertain', 'notes': 'Scripted uncertainty',
             'citations': [ref]} for item in prepared['checklist']['items']]}
        data(actor.request('PUT', path + '/handover-report', {
            'operation_id': started['report_operation_id'], 'report': report}))
        snapshot = self.collect('H01', prepared)
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertEqual(len(prepare(snapshot)['citations']), len(report['items']))
        self.assertGreater(len(report['items']), 1)
        self.assertEqual(len(snapshot['citation_sources']), 1)
        self.assertEqual(snapshot['handover_report']['report']['summary'], report['summary'])
        data(self.user.request('PUT', self.base() + '/files/' + ref['file_id'] + '/remote-ai-policy',
                               {'policy': 'internal_only', 'approved_version_ids': []}))
        denied = self.collect('H01', prepared)
        self.assertFalse(denied['capture']['complete'])
        self.assertNotIn('handover_report', denied)
        with self.assertRaises(ValueError):
            prepare(denied)

    def test_capture_archive_preview_never_confirms_or_moves(self):
        prepared = self.setup_case('A06')
        actor, path = prepared['actor'], prepared['path']
        data(actor.request('POST', path + '/run/start'))
        items = []
        for version in prepared['body']['versions']:
            source = next(v for v in prepared['mapping'].values() if v['version_id'] == version['version_id'])
            items.append({**version, 'source_directory_id': source['directory_id'],
                          'target_directory_id': None, 'reason': 'Unresolved protocol fixture'})
        plan = data(actor.request('PUT', path + '/archive-plan', {'expected_revision': 0,
            'policy_revision': prepared['policy']['revision'], 'items': items}))
        before = data(actor.request('GET', self.base() + '/files'))
        snapshot = self.collect('A06', prepared)
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertEqual(snapshot['archive_plan'], plan)
        self.assertIsNone(snapshot['archive_plan']['decision'])
        self.assertEqual(snapshot['run_result']['model_calls'], 0)
        self.assertEqual(self.count('SELECT COUNT(*) FROM archive_decisions'), 0)
        self.assertEqual(data(actor.request('GET', self.base() + '/files')), before)

    def test_capture_committed_archive_receipt_without_reexecution(self):
        prepared = self.setup_case('A01')
        actor, path = prepared['actor'], prepared['path']
        data(actor.request('POST', path + '/run/start'))
        target = prepared['policy']['targets'][0]['directory_id']
        items = []
        for version in prepared['body']['versions']:
            source = next(v for v in prepared['mapping'].values() if v['version_id'] == version['version_id'])
            items.append({**version, 'source_directory_id': source['directory_id'],
                          'target_directory_id': target, 'reason': 'Protocol fixture, not classification quality'})
        plan = data(actor.request('PUT', path + '/archive-plan', {'expected_revision': 0,
            'policy_revision': prepared['policy']['revision'], 'items': items}))
        # Explicit test setup only: collector is not responsible for this decision.
        confirmed = data(actor.request('POST', path + '/archive-plan/decision', {
            'plan_id': plan['id'], 'revision': plan['revision'],
            'content_sha256': plan['content_sha256'], 'decision': 'confirm'}))
        executed = data(actor.request('POST', path + '/archive-plan/execute', {
            'plan_id': confirmed['id'], 'revision': confirmed['revision'],
            'operation_id': confirmed['operation_id']}))
        receipt = {key: executed[key] for key in (
            'operation_id', 'plan_id', 'revision', 'status', 'moved_count', 'audit_id')}
        before = self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute'")
        snapshot = self.collect('A01', prepared)
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertEqual(snapshot['archive_receipt'], receipt)
        self.assertEqual(snapshot['run_result']['archive_operation'], receipt)
        self.assertEqual(snapshot['archive_plan']['state'], 'executed')
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute'"), before)
        self.assertIsNone(prepare(snapshot)['business']['verdict'])

    def test_capture_admission_rejection_preserves_absent_execution(self):
        prepared = self.setup_case('Q08')
        snapshot = self.collect('Q08', prepared, 'admission_rejection')
        self.assertTrue(snapshot['capture']['complete'])
        self.assertEqual(snapshot['admission_http'], 404)
        self.assertIsNone(snapshot['run_result'])
        self.assertEqual(snapshot['capture']['reads'], [])
        self.assertEqual(self.count('SELECT COUNT(*) FROM agent_runs'), 0)
        self.assertEqual(prepare(snapshot)['citations'], [])

    def test_capture_failed_run_keeps_failure_not_a_synthetic_answer(self):
        prepared = self.setup_case('Q02')
        actor, path = prepared['actor'], prepared['path']
        data(actor.request('POST', path + '/run/start'))
        data(actor.request('POST', path + '/run/finish', {
            'status': 'failed', 'result': None, 'error_code': 'provider_unavailable'}))
        snapshot = self.collect('Q02', prepared)
        self.assertTrue(snapshot['capture']['complete'], snapshot['capture'])
        self.assertEqual(snapshot['run_result']['state'], 'failed')
        self.assertEqual(snapshot['run_result']['error_code'], 'provider_unavailable')
        self.assertIsNone(snapshot['run_result']['result'])
        self.assertIsNone(prepare(snapshot)['business']['verdict'])


if __name__ == '__main__':
    # Do not rerun inherited loader/feature suites as collector tests.
    suite = unittest.TestSuite(CaptureE2E(name) for name in sorted(CaptureE2E.__dict__)
                               if name.startswith('test_capture_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
