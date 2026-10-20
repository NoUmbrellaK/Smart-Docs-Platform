"""CLI lifecycle test; synthetic HTTP reviewer is NOT real human confirmation."""
from contextlib import redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from benchmark_review import read_private
from m1_http_test import HttpClient, ServerProcess, data, login
from m3_agent_http_test import tool
import m5_benchmark_execute_test as execute_test


class CliTests(unittest.TestCase):
    provider = execute_test.ExecuteE2E.provider

    def args(self, output, *extra):
        return ['--case', 'A01', '--output', str(output), '--code-revision', 'test-checkout',
                '--environment', 'isolated SQL / fake provider', '--max-calls', '1',
                '--hold-seconds', '30', *extra]

    def test_help_does_not_require_database_or_key(self):
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('benchmark_cli.py')),
                                 '--help'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--case', result.stdout)

    def test_no_opt_in_and_bad_admission_mode_stop_before_artifacts(self):
        cli = importlib.import_module('benchmark_cli')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,
                {'DEEPSEEK_API_KEY': 'do-not-read-this-key'}), redirect_stdout(io.StringIO()) as output:
            destination = Path(folder) / 'denied'
            self.assertEqual(cli.main(self.args(destination)), 2)
            self.assertEqual(cli.main(self.args(destination, '--admission-only')), 2)
            self.assertFalse(destination.exists())
            self.assertEqual(os.environ['DEEPSEEK_API_KEY'], 'do-not-read-this-key')
            self.assertNotIn('do-not-read-this-key', output.getvalue())

    def test_interrupt_during_partial_setup_stops_owned_child_and_removes_temp(self):
        cli = importlib.import_module('benchmark_cli')
        with tempfile.TemporaryDirectory() as folder, patch.dict(
                os.environ, {'DEEPSEEK_API_KEY': 'synthetic-execute-key'}):
            temp = tempfile.TemporaryDirectory(dir=folder)
            temp_path = Path(temp.name)
            server = ServerProcess('/usr/bin/sleep', temp_path / 'logs')
            process = server.process = subprocess.Popen(['/usr/bin/sleep', '30'])

            def interrupted(cls):
                cls.server, cls.temp = server, temp
                raise KeyboardInterrupt()

            try:
                with patch.object(cli.FixedInputsE2E, 'setUpClass', classmethod(interrupted)):
                    with self.assertRaises(KeyboardInterrupt):
                        cli.main(self.args(Path(folder) / 'interrupted', '--live'))
                self.assertIsNotNone(process.poll(), 'owned child must not survive Ctrl-C')
                self.assertFalse(temp_path.exists())
            finally:
                server.stop()
                temp.cleanup()

    def test_setup_failure_retains_private_diagnostic_without_exception_text(self):
        cli = importlib.import_module('benchmark_cli')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                'DEEPSEEK_API_KEY': 'synthetic-execute-key',
                'SMARTDOCS_MYSQL_SOCKET': '/not-an-isolated-test-socket'}), redirect_stdout(io.StringIO()) as output:
            destination = Path(folder) / 'failed'
            self.assertEqual(cli.main(self.args(destination, '--live')), 1)
            failure = read_private(destination / 'failure.json')
            summary = read_private(destination / 'summary.json')
            self.assertEqual(failure['error'], 'fixture_or_execution_failed')
            self.assertEqual(failure['errors'], 1)
            self.assertEqual(summary['state'], 'unknown')
            self.assertFalse(summary['capture_complete'])
            self.assertNotIn('synthetic-execute-key', output.getvalue())
            self.assertFalse((destination / 'review-access.json').exists())

    def test_archive_cli_keeps_http_alive_for_separate_review_then_captures_receipt(self):
        cli = importlib.import_module('benchmark_cli')
        reviewer_errors, reviewed, access_seen = [], threading.Event(), []

        def respond(body):
            self.assertNotIn('DEEPSEEK_API_KEY', os.environ)
            contexts = [json.loads(m['content']) for m in body['messages']
                        if m['role'] == 'user' and m['content'].startswith('{')]
            context = next(c['archive_context'] for c in contexts if 'archive_context' in c)
            return tool('create_archive_plan', {'expected_revision': 0, 'policy_revision': context['revision'],
                'items': [{**source, 'target_directory_id': context['targets'][0]['directory_id'],
                           'reason': 'Protocol fixture only'} for source in context['sources']]})

        with tempfile.TemporaryDirectory() as folder, self.provider(respond), patch.dict(
                os.environ, {'DEEPSEEK_API_KEY': 'synthetic-execute-key'}):
            destination = Path(folder) / 'run'

            def synthetic_reviewer():
                try:
                    deadline = time.monotonic() + 40
                    access_path = destination / 'review-access.json'
                    while not access_path.exists() and time.monotonic() < deadline:
                        time.sleep(.05)
                    access = read_private(access_path)
                    access_seen.append(access)
                    client = HttpClient(access['backend_port'])
                    login(client, access['username'], access['password'])
                    path = access['task_path']
                    plan = data(client.request('GET', path + '/archive-plan'))
                    self.assertIsNone(plan['decision'])
                    # Explicit synthetic actor: these POSTs are test setup, never CLI behavior.
                    confirmed = data(client.request('POST', path + '/archive-plan/decision', {
                        'plan_id': plan['id'], 'revision': plan['revision'],
                        'content_sha256': plan['content_sha256'], 'decision': 'confirm'}))
                    data(client.request('POST', path + '/archive-plan/execute', {
                        'plan_id': confirmed['id'], 'revision': confirmed['revision'],
                        'operation_id': confirmed['operation_id']}))
                    reviewed.set()
                except Exception as error:
                    reviewer_errors.append(type(error).__name__)

            reviewer = threading.Thread(target=synthetic_reviewer)
            reviewer.start()
            try:
                with redirect_stdout(io.StringIO()) as output:
                    status = cli.main(self.args(destination, '--live'), run_kind='transport_fixture')
            finally:
                reviewer.join(45)
            self.assertFalse(reviewer.is_alive())
            self.assertEqual(reviewer_errors, [])
            self.assertTrue(reviewed.is_set())
            self.assertEqual(status, 0, output.getvalue())
            self.assertEqual(len(self.requests), 1)
            initial = read_private(destination / 'initial' / 'snapshot.json')
            final = read_private(destination / 'after-review.json')
            summary = read_private(destination / 'summary.json')
            self.assertEqual(initial['run_result']['state'], 'waiting_confirmation')
            self.assertEqual(final['run_result']['kind'], 'archive_operation')
            self.assertEqual(final['archive_receipt']['status'], 'executed')
            self.assertTrue(final['capture']['complete'])
            self.assertEqual(summary['state'], 'completed')
            self.assertIsNone(summary['business_verdict'])
            self.assertFalse((destination / 'review-access.json').exists())
            self.assertFalse((destination / 'failure.json').exists())
            for path in destination.rglob('*'):
                if path.is_file():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    text = path.read_text()
                    self.assertNotIn('synthetic-execute-key', text)
                    self.assertNotIn(access_seen[0]['password'], text)
            self.assertNotIn(access_seen[0]['password'], output.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
