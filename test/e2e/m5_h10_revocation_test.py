"""Real H10 Agent/SQL revocation; only the provider transport is scripted."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from benchmark_h10 import execute_h10
from benchmark_review import read_private
from m3_agent_http_test import tool
from m3_fixed_inputs_test import pair
from m5_benchmark_execute_test import ExecuteE2E


class H10E2E(ExecuteE2E):
    def run_h10(self, *, read_design=True, cite_design=False, reject_body=False):
        prepared = self.setup_case('H10')
        req, design = (pair(prepared['loaded'][ref]) for ref in ('requirements@1', 'design@1'))

        def respond(body):
            self.assertEqual([m['content'] for m in body['messages']
                              if m['content'] == prepared['body']['question']],
                             [prepared['body']['question']])
            n = len(self.requests)
            if n == 1:
                return tool('read_document_version', {**req, 'range': {}})
            if n == 2:
                return tool('read_document_version', {**design, **({'range': {}} if read_design else {})}, sequence=2)
            if reject_body:
                return httpx.Response(429, stream=httpx.ByteStream(b''))
            results = [json.loads(m['content'])['data'] for m in body['messages'] if m['role'] == 'tool']
            refs = [{k: chunk[k] for k in ('file_id', 'version_id', 'chunk_id')}
                    for result in results for chunk in result.get('chunks', [])]
            contexts = [json.loads(m['content']) for m in body['messages']
                        if m['role'] == 'user' and m['content'].startswith('{')]
            operation = next(c['report_operation_id'] for c in contexts if 'report_operation_id' in c)
            checklist = next(c['handover_checklist'] for c in contexts if 'handover_checklist' in c)
            return tool('save_handover_report', {'operation_id': operation, 'report': {
                'summary': 'H10 private report sentinel: offline protocol fixture.',
                'limitations': 'No model semantic evaluation.', 'recommendations': 'Review manually.',
                'items': [{'key': item['key'],
                           'status': 'found' if item['key'] == 'requirements' else 'uncertain',
                           'notes': 'Protocol fixture only.',
                           'citations': ([refs[0]] if item['key'] == 'requirements' else
                                         [refs[1]] if item['key'] == 'design' and cite_design else [])}
                          for item in checklist['items']]}}, sequence=3)

        with tempfile.TemporaryDirectory(dir=self.root) as folder, self.provider(respond):
            directory = Path(folder)
            snapshot, probe = asyncio.run(execute_h10(self, prepared, {
                'code_revision': 'test-checkout', 'recorded_at': 'contract-test',
                'environment': 'isolated SQL / fake provider', 'model': 'transport fixture',
                'evidence_path': 'm5_h10_revocation_test.py'}, directory,
                backend_port=self.server.port, api_key='synthetic-execute-key',
                max_calls=3, run_kind='transport_fixture'))
            if self.provider_errors:
                raise self.provider_errors[0]
            self.assertNotEqual(probe['state'], 'not_run', 'H10 interleaving must actually run')
            self.assertEqual(read_private(directory / 'h10-revocation.json'), probe)
            self.assertEqual(read_private(directory / 'initial/snapshot.json'), snapshot)
            wire = [json.loads(line) for line in (directory / 'h10-wire.jsonl').read_text().splitlines()]
            self.assertEqual(len(wire), len(self.requests))
            self.assertEqual([row['body_sources'] for row in wire],
                             [[], ['requirements@1'], ['requirements@1', 'design@1'] if read_design else ['requirements@1']])
            for f in directory.rglob('*'):
                if f.is_file():
                    self.assertEqual(f.stat().st_mode & 0o777, 0o600)
                    self.assertNotIn('synthetic-execute-key', f.read_text())
            if snapshot.get('handover_report'):
                self.assertEqual(len(snapshot['handover_report']['report']['items'][1]['citations']), int(cite_design))
            return prepared, snapshot, probe

    def test_h10_uncited_body_revocation_denies_existing_report_without_regeneration(self):
        # A source dependency based only on citations, or missing post-read checks, breaks this.
        prepared, snapshot, probe = self.run_h10()
        self.assertEqual(probe['state'], 'boundary_verified')
        self.assertTrue(all(probe['preconditions'].values()))
        self.assertEqual(probe['before_http'], [200, 200, 200])
        self.assertEqual([r['http'] for r in probe['after']], [409, 409, 409])
        self.assertTrue(all(r['content_matches'] == 0 for r in probe['after']))
        self.assertEqual(probe['reports_before'], probe['reports_after'])
        self.assertEqual(probe['reports_after'], 1)
        self.assertEqual(probe['archive_plans'], 0)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(snapshot['handover_report']['state'], 'draft')
        self.assertEqual(len(snapshot['handover_report']['source_versions']), 2)
        self.assertIsNone(probe['business_verdict'])

    def test_h10_metadata_only_does_not_fake_body_precondition_or_revoke(self):
        prepared, _, probe = self.run_h10(read_design=False)
        self.assertEqual(probe['state'], 'preconditions_not_met')
        self.assertFalse(probe['preconditions']['design_body_sent'])
        self.assertEqual(probe['after'], [])
        self.assertEqual(prepared['actor'].request('GET', prepared['path'] + '/handover-report').status, 200)

    def test_h10_explicit_design_citation_is_preserved_and_not_counted_as_uncited(self):
        prepared, _, probe = self.run_h10(cite_design=True)
        self.assertEqual(probe['state'], 'preconditions_not_met')
        self.assertFalse(probe['preconditions']['design_uncited'])
        self.assertEqual(probe['after'], [])
        self.assertEqual(prepared['actor'].request('GET', prepared['path'] + '/handover-report/markdown').status, 200)

    def test_h10_failed_provider_request_is_not_successful_body_admission_evidence(self):
        prepared, snapshot, probe = self.run_h10(reject_body=True)
        self.assertEqual(snapshot['run_result']['state'], 'failed')
        self.assertEqual(probe['state'], 'preconditions_not_met')
        self.assertFalse(probe['preconditions']['design_body_sent'])
        self.assertEqual(probe['after'], [])
        self.assertEqual(self.user.request('GET', self.version_path(prepared['loaded']['design@1']) + '/remote-ai/document').status, 200)


if __name__ == '__main__':
    suite = unittest.TestSuite(H10E2E(n) for n in dir(H10E2E) if n.startswith('test_h10_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
