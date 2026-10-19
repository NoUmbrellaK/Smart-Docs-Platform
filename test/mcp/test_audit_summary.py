"""Local trace accounting, not a provider-cost or model-quality benchmark."""
import asyncio
import copy
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import httpx
from smartdocs.deepseek import DeepSeekModel, open_audit


TASK = 'a' * 32
USAGE = {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30,
         'prompt_cache_hit_tokens': 5, 'prompt_cache_miss_tokens': 15}


def events():
    return [
        {'event': 'task_start'},
        {'event': 'model_attempt', 'sequence': 1, 'request_id': 'b' * 32,
         'model': 'deepseek-flash', 'request_bytes': 200, 'max_tokens': 512},
        {'event': 'model_result', 'sequence': 1, 'request_id': 'b' * 32,
         'model': 'deepseek-flash', 'reason': 'success', 'duration_ms': 200, 'usage': USAGE},
        {'event': 'tool_attempt', 'sequence': 1, 'tool': 'search_documents'},
        {'event': 'tool_result', 'sequence': 1, 'tool': 'search_documents', 'status': 'success',
         'duration_ms': 7, 'item_count': 1, 'chunk_count': 0, 'query_changed': False},
        {'event': 'task_outcome', 'state': 'completed'},
    ]


class AuditSummaryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('smartdocs.audit_summary'), 'local audit summary missing')
        self.summary = importlib.import_module('smartdocs.audit_summary')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'trace.jsonl'

    def write(self, records):
        self.path.unlink(missing_ok=True)
        with open_audit(self.path, TASK) as record:
            for event in records:
                record(event)

    def cli(self, path=None):
        return subprocess.run([sys.executable, '-B', '-m', 'smartdocs.audit_summary', str(path or self.path)],
            env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': os.environ['PYTHONPATH']},
            capture_output=True, text=True, timeout=10)

    def test_complete_trace_separates_model_usage_from_tool_time_and_business_acceptance(self):
        self.write(events())
        before = self.path.read_bytes()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['trace_complete'])
        self.assertEqual(value['task_id'], TASK)
        self.assertEqual(value['outcome'], {'state': 'completed'})
        self.assertEqual(value['model']['attempts'], 1)
        self.assertEqual(value['model']['duration_ms_observed'], 200)
        self.assertEqual(value['model']['usage_total'], USAGE)
        self.assertEqual(value['tools']['search_documents'], {
            'attempts': 1, 'results': 1, 'unresolved': 0, 'duration_ms_observed': 7})
        self.assertIsNone(value['cost_estimate'])
        self.assertEqual(value['business_acceptance'], 'not_evaluated')
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_or_invalid_usage_is_unknown_not_free(self):
        for usage in (None, {}, {**USAGE, 'total_tokens': 99}, {**USAGE, 'prompt_tokens': True}):
            with self.subTest(usage=usage):
                records = copy.deepcopy(events())
                records[2]['usage'] = usage
                self.write(records)
                value = self.summary.summarize(self.path)
                self.assertTrue(value['trace_complete'])
                self.assertEqual(value['model']['usage_unknown_calls'], 1)
                self.assertEqual(value['model']['usage_known_calls'], 0)
                self.assertIsNone(value['model']['usage_total'])
                self.assertIsNone(value['cost_estimate'])

    def test_partial_usage_and_interrupted_attempt_preserve_lower_bound(self):
        records = events()[:-1]
        records.append({**events()[1], 'sequence': 2, 'request_id': 'c' * 32})
        self.write(records)
        value = self.summary.summarize(self.path)
        self.assertFalse(value['trace_complete'])
        self.assertIsNone(value['outcome'])
        self.assertEqual(value['model']['attempts'], 2)
        self.assertEqual(value['model']['unresolved'], 1)
        self.assertEqual(value['model']['usage_unknown_calls'], 1)
        self.assertEqual(value['model']['usage_observed'], USAGE)
        self.assertIsNone(value['model']['usage_total'])

    def test_absent_cache_breakdown_never_becomes_zero_cache_miss(self):
        records = events()
        records[2] = {**records[2], 'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}}
        self.write(records)
        value = self.summary.summarize(self.path)
        self.assertEqual(value['model']['usage_total'], records[2]['usage'])
        self.assertIsNone(value['cost_estimate'])

    def test_pairing_rejects_duplicate_foreign_or_inconsistent_records_without_echo(self):
        variants = []
        for index, update in ((2, {'sequence': 2}), (2, {'request_id': 'c' * 32}),
                              (4, {'tool': 'read_document_version'}), (1, {'sequence': True}),
                              (2, {'duration_ms': -1}), (1, {'model': 'PRIVATE-SECRET'})):
            records = copy.deepcopy(events()); records[index].update(update); variants.append(records)
        variants.extend([events()[:3] + [events()[2]] + events()[3:],
                         events() + [events()[5]], events()[0:1] + events()[2:]])
        for records in variants:
            with self.subTest(records=records):
                self.write(records)
                with self.assertRaises(ValueError): self.summary.summarize(self.path)
        self.write(events())
        lines = self.path.read_text().splitlines()
        lines[2] = lines[2].replace(TASK, 'd' * 32)
        self.path.write_text('\n'.join(lines) + '\n')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), {'error': 'audit_unavailable_or_invalid'})
        self.assertEqual(result.stderr, '')
        self.assertNotIn(str(self.path), result.stdout)

    def test_truncated_tail_is_incomplete_and_complete_bad_line_is_rejected(self):
        self.write(events()[:-1])
        with self.path.open('ab') as output: output.write(b'{"PRIVATE-SECRET":')
        value = self.summary.summarize(self.path)
        self.assertTrue(value['truncated_tail']); self.assertFalse(value['trace_complete'])
        self.assertIsNone(value['model']['usage_total'])
        with self.path.open('ab') as output: output.write(b'\n')
        with self.assertRaises(ValueError): self.summary.summarize(self.path)
        for raw in (b'{}\n', b'{"event":"task_start","event":"task_outcome"}\n',
                    b'{"x":NaN}\n', b'x' * 262145, b' ' * 4097 + b'\n'):
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError): self.summary.summarize(self.path)

    def test_reader_refuses_symlinks_fifo_and_insecure_files(self):
        self.write(events())
        link = self.path.parent / 'link'; link.symlink_to(self.path)
        fifo = self.path.parent / 'fifo'; os.mkfifo(fifo, 0o600)
        for path in (link, fifo, self.path.parent):
            with self.subTest(path=path):
                with self.assertRaises(ValueError): self.summary.summarize(path)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError): self.summary.summarize(self.path)

    def test_no_model_resume_is_zero_usage_without_claiming_free_business_success(self):
        self.write([{'event': 'archive_resume'}, {'event': 'task_outcome', 'state': 'unknown',
                                               'error_code': 'PRIVATE-SECRET'}])
        value = self.summary.summarize(self.path)
        self.assertTrue(value['trace_complete'])
        self.assertEqual(value['model']['usage_total'], {
            'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})
        self.assertEqual(value['outcome'], {'state': 'unknown', 'error_code': 'dependency_failed'})
        self.assertNotIn('PRIVATE-SECRET', json.dumps(value))

    def test_actual_provider_audit_writer_with_local_transport(self):
        async def run():
            with open_audit(self.path, TASK) as record:
                record({'event': 'task_start'})
                response = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'PRIVATE-BODY'}}],
                            'usage': USAGE}
                model = DeepSeekModel('PRIVATE-KEY', record, transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, stream=httpx.ByteStream(json.dumps(response).encode()))))
                await model(messages=[], tools=[], max_tokens=512, timeout=1)
                record({'event': 'task_outcome', 'state': 'completed'})
        asyncio.run(run())
        value = self.summary.summarize(self.path)
        self.assertTrue(value['trace_complete'])
        self.assertEqual(value['model']['usage_total'], USAGE)
        self.assertNotIn('PRIVATE', json.dumps(value))


if __name__ == '__main__': unittest.main()
