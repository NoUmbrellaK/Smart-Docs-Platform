"""Offline synthetic snapshots test review accounting, not model performance."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'e2e/benchmark_review.py'
CITATION = {'file_id': 'a' * 32, 'version_id': 'b' * 32, 'chunk_id': 'c' * 64}
RECEIPT = {'id': 'd' * 32, 'version': 1, 'url': '/synthetic/report',
           'markdown_url': '/synthetic/report/markdown'}


def snapshot(case='Q01'):
    return {'case_id': case, 'run_kind': 'transport_fixture',
            'metadata': {'code_revision': 'offline-test', 'recorded_at': '2026-10-19',
                         'environment': 'synthetic unit test', 'model': 'scripted',
                         'evidence_path': 'unit-test-only'},
            'run_result': {'state': 'completed', 'model_calls': 1,
                           'result': {'answer': 'Synthetic claim', 'citations': [CITATION]}}}


class BenchmarkReviewTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'offline review entrypoint not implemented')
        spec = importlib.util.spec_from_file_location('benchmark_review', SCRIPT)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def test_prepare_preserves_every_occurrence_without_semantic_auto_pass(self):
        source = snapshot('H01')
        source['run_result'] = {'state': 'completed', 'kind': 'handover_report', 'result': None,
                                'handover_report': copy.deepcopy(RECEIPT)}
        source['handover_report'] = {**RECEIPT, 'report': {'summary': 'Synthetic report', 'items': [
            {'key': 'one', 'notes': 'First claim', 'citations': [CITATION]},
            {'key': 'two', 'notes': 'Second claim', 'citations': [CITATION, {'bad': 'source'}]}]}}
        original = copy.deepcopy(source)
        review = self.tool.prepare(source)
        self.assertEqual(source, original)
        self.assertEqual(len(review['citations']), 3)
        self.assertEqual(len({r['pointer'] for r in review['citations']}), 3)
        self.assertEqual(review['citations'][1]['claim'], 'Second claim')
        self.assertEqual(review['citations'][2]['citation'], {'bad': 'source'})
        self.assertEqual(review['business']['verdict'], None)
        self.assertTrue(all(r['assessment']['located'] is None for r in review['citations']))
        result = self.tool.summarize(source, review)
        self.assertEqual((result['citations_total'], result['citations_reviewed']), (3, 0))
        self.assertIsNone(result['citation_accuracy'])

    def test_report_receipt_mismatch_cannot_replace_actual_citation_denominator(self):
        source = snapshot('H01')
        source['run_result'] = {'state': 'completed', 'kind': 'handover_report', 'result': None,
                                'handover_report': copy.deepcopy(RECEIPT)}
        source['handover_report'] = {**RECEIPT, 'report': {'items': [
            {'key': 'one', 'notes': 'Uncertain', 'citations': []}]}}
        self.assertEqual(self.tool.prepare(source)['citations'], [])
        for field in RECEIPT:
            bad = copy.deepcopy(source)
            bad['handover_report'][field] = 2 if field == 'version' else 'other'
            with self.subTest(field=field), self.assertRaises(ValueError): self.tool.prepare(bad)
        source['run_result']['handover_report'] = {}
        with self.assertRaises(ValueError): self.tool.prepare(source)

    def test_summary_counts_wrong_location_and_unsupported_claim_not_only_valid_ids(self):
        source = snapshot()
        source['run_result']['result']['citations'] *= 3
        review = self.tool.prepare(source)
        for row, located, supported, error in zip(review['citations'],
                [True, False, True], [True, True, False], [None, 'wrong_location', 'unsupported_claim']):
            row['assessment'] = {'located': located, 'supported': supported,
                                 'error': error, 'notes': 'Manually checked synthetic text'}
        review['business'] = {'verdict': 'fail', 'reviewer': 'unit tester', 'notes': 'Two bad references'}
        result = self.tool.summarize(source, review)
        self.assertEqual((result['citations_total'], result['citations_reviewed'], result['citations_correct']), (3, 3, 1))
        self.assertAlmostEqual(result['citation_accuracy'], 1 / 3)
        self.assertEqual(result['citation_errors'], {'wrong_location': 1, 'unsupported_claim': 1})
        self.assertEqual(result['declared_run_kind'], 'transport_fixture')
        self.assertFalse(result['live_provider_declared'])

    def test_missing_report_and_removed_changed_or_reordered_rows_are_rejected(self):
        source = snapshot()
        review = self.tool.prepare(source)
        for mutation in ('drop', 'citation', 'claim', 'expected', 'source'):
            bad_source, bad_review = copy.deepcopy(source), copy.deepcopy(review)
            if mutation == 'drop': bad_review['citations'] = []
            elif mutation == 'citation': bad_review['citations'][0]['citation']['version_id'] = 'd' * 32
            elif mutation == 'claim': bad_review['citations'][0]['claim'] = 'Different claim'
            elif mutation == 'expected': bad_review['expected'] = {}
            else: bad_source['run_result']['result']['answer'] = 'Changed output'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.tool.summarize(bad_source, bad_review)
        source = snapshot('H01')
        source['run_result'] = {'state': 'completed', 'kind': 'handover_report', 'result': None}
        with self.assertRaises(ValueError): self.tool.prepare(source)

    def test_incomplete_or_inconsistent_assessment_cannot_be_business_pass(self):
        source = snapshot()
        review = self.tool.prepare(source)
        review['business'] = {'verdict': 'pass', 'reviewer': 'tester', 'notes': 'Reviewed'}
        with self.assertRaises(ValueError): self.tool.summarize(source, review)
        for assessment in (
                {'located': True, 'supported': False, 'error': None, 'notes': 'Bad'},
                {'located': 1, 'supported': True, 'error': None, 'notes': 'Bad'},
                {'located': True, 'supported': True, 'error': 'wrong_source', 'notes': 'Bad'}):
            review['citations'][0]['assessment'] = assessment
            with self.subTest(assessment=assessment), self.assertRaises(ValueError):
                self.tool.summarize(source, review)
        source['run_kind'] = 'not_run'
        source['run_result'] = None
        review = self.tool.prepare(source)
        review['business'] = {'verdict': 'pass', 'reviewer': 'tester', 'notes': 'No run'}
        with self.assertRaises(ValueError): self.tool.summarize(source, review)

    def test_empty_citations_and_denied_admission_remain_explicit(self):
        source = snapshot('Q08')
        source.update(run_kind='admission_rejection', run_result=None, admission_http=404)
        review = self.tool.prepare(source)
        review['business'] = {'verdict': 'pass', 'reviewer': 'tester', 'notes': 'Verified refusal evidence'}
        result = self.tool.summarize(source, review)
        self.assertEqual(result['citations_total'], 0)
        self.assertIsNone(result['citation_accuracy'])
        self.assertEqual(result['business_verdict'], 'pass')
        self.assertFalse(result['live_provider_declared'])

    def test_completed_answer_cannot_omit_result_or_mislabel_run_as_not_run(self):
        for result in (None, {}, {'answer': 'Claim'}):
            source = snapshot()
            source['run_result']['result'] = result
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.tool.prepare(source)
        source = snapshot()
        source['run_kind'] = 'not_run'
        with self.assertRaises(ValueError): self.tool.prepare(source)

    def test_cli_private_exclusive_files_no_raw_output_and_strict_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source.json'; output = root / 'review.json'
            source.write_text(json.dumps(snapshot())); source.chmod(0o600)
            def cli(*args):
                return subprocess.run([sys.executable, '-B', str(SCRIPT), *map(str, args)],
                                      capture_output=True, text=True, timeout=5)
            prepared = cli('prepare', source, output)
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            saved = output.read_bytes()
            self.assertNotIn('Synthetic claim', prepared.stdout + prepared.stderr)
            self.assertEqual(cli('prepare', source, output).returncode, 2)
            self.assertEqual(output.read_bytes(), saved)
            result = cli('summarize', source, output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['citations_total'], 1)
            self.assertNotIn('Synthetic claim', result.stdout + result.stderr)
            source.chmod(0o644)
            self.assertEqual(cli('summarize', source, output).returncode, 2)
            source.chmod(0o600)
            link = root / 'link.json'; link.symlink_to(source)
            self.assertEqual(cli('summarize', link, output).returncode, 2)
            source.write_text('{"case_id":"Q01","case_id":"Q02"}')
            self.assertEqual(cli('prepare', source, root / 'bad.json').returncode, 2)
            self.assertFalse((root / 'bad.json').exists())


if __name__ == '__main__': unittest.main()
