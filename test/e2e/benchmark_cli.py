"""One frozen benchmark case in disposable SQL; never opt in or confirm implicitly."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
import unittest

from benchmark_capture import capture, write_snapshot
from benchmark_execute import execute
from m1_http_test import data
from m3_fixed_inputs_test import FixedInputsE2E, TASKS
from smartdocs.deepseek import MODEL


def main(argv=None, *, run_kind='live_provider'):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=[case['id'] for case in TASKS])
    parser.add_argument('--output', required=True, help='new directory under a trusted private parent')
    parser.add_argument('--code-revision', required=True)
    parser.add_argument('--environment', required=True, help='actual hardware/index/dependency configuration')
    parser.add_argument('--max-calls', required=True, type=int, choices=range(1, 9))
    parser.add_argument('--hold-seconds', required=True, type=int, choices=range(0, 601),
                        metavar='0..600', help='keep archive HTTP alive for separate user actions, then clean up')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='requires separate data/fee authorization and key in environment')
    mode.add_argument('--admission-only', action='store_true', help='Q08/Q09/A09 only; no key or model')
    args = parser.parse_args(argv)
    case = next(case for case in TASKS if case['id'] == args.case)
    rejected = case['id'] in {'Q08', 'Q09', 'A09'}
    if (not (args.live or args.admission_only) or args.admission_only != rejected
            or run_kind not in {'live_provider', 'transport_fixture'}
            or not args.code_revision.strip() or not args.environment.strip()):
        print('{"state":"not_started","error":"explicit_matching_mode_required"}')
        return 2
    # Pop before starting any fixture child; never source a file or inherit the key.
    key = os.environ.pop('DEEPSEEK_API_KEY', '') if args.live else ''
    if not args.live and 'DEEPSEEK_API_KEY' in os.environ:
        del os.environ['DEEPSEEK_API_KEY']
    if args.live and not key:
        print('{"state":"not_started","error":"provider_key_required"}')
        return 2
    directory = Path(args.output)
    try:
        directory.mkdir(mode=0o700)
    except OSError:
        print('{"state":"not_started","error":"new_private_output_required"}')
        return 2
    metadata = {'code_revision': args.code_revision,
                'recorded_at': datetime.now(timezone.utc).isoformat(),
                'environment': args.environment, 'model': MODEL if args.live else 'no model',
                'evidence_path': str(directory)}
    kind = 'admission_rejection' if rejected else run_kind
    summary = {'case_id': case['id'], 'run_kind': kind, 'state': 'not_started',
               'business_verdict': None, 'capture_complete': False, 'review_wait': 'not_requested'}
    access_path = directory / 'review-access.json'

    class Case(FixedInputsE2E):
        @classmethod
        def cleanup_owned(cls):
            server, temp = cls.__dict__.get('server'), cls.__dict__.get('temp')
            try:
                if server is not None:
                    server.stop()
            finally:
                if temp is not None:
                    temp.cleanup()

        @classmethod
        def setUpClass(cls):
            # Only touch resources actually owned by this invocation.
            cls.addClassCleanup(cls.cleanup_owned)
            super().setUpClass()

        def runTest(self):
            prepared = self.prepare_case(case)
            snapshot = asyncio.run(execute(case['id'], prepared, metadata, directory / 'initial',
                backend_port=self.server.port, api_key=key, max_calls=args.max_calls, run_kind=kind))
            state = snapshot['run_result']['state'] if snapshot['run_result'] else 'rejected'
            if state in {'waiting_confirmation', 'waiting_execution'} and args.hold_seconds:
                owner = data(prepared['actor'].request('GET', '/api/v1/me'))['user']
                access = {'backend_port': self.server.port, 'url': f'http://127.0.0.1:{self.server.port}/app.html',
                          'username': owner['username'], 'password': self.password,
                          'project_id': self.project['id'], 'task_path': prepared['path']}
                pending_access = directory / 'review-access.pending'
                write_snapshot(pending_access, access)
                pending_access.rename(access_path)  # Publish only a complete private file.
                print(json.dumps({'case_id': case['id'], 'state': state,
                                  'review_access_file': str(access_path),
                                  'expires_after_seconds': args.hold_seconds}), flush=True)
                deadline = time.monotonic() + args.hold_seconds
                summary['review_wait'] = 'deadline'
                while time.monotonic() < deadline:
                    state = data(prepared['actor'].request('GET', prepared['path']))['state']
                    if state not in {'waiting_confirmation', 'waiting_execution'}:
                        summary['review_wait'] = 'state_changed'
                        break
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
                snapshot = capture(case['id'], prepared, metadata, run_kind=kind)
                write_snapshot(directory / 'after-review.json', snapshot)
                state = snapshot['run_result']['state'] if snapshot['run_result'] else 'unknown'
            summary.update(state=state, capture_complete=snapshot['capture']['complete'])

    logging.disable(logging.CRITICAL)
    try:
        result = unittest.TestResult()
        unittest.TestSuite([Case('runTest')]).run(result)
        if not result.wasSuccessful() or result.testsRun != 1:
            summary.update(state='unknown', capture_complete=False)
            write_snapshot(directory / 'failure.json', {'error': 'fixture_or_execution_failed',
                'errors': len(result.errors), 'failures': len(result.failures),
                'details': 'not logged to avoid leaking credentials; existing artifacts retained'})
        write_snapshot(directory / 'summary.json', summary)
    finally:
        try:
            # KeyboardInterrupt bypasses unittest's tearDownClass/class cleanup.
            Case.cleanup_owned()
        finally:
            key = ''
            access_path.unlink(missing_ok=True)
            (directory / 'review-access.pending').unlink(missing_ok=True)
    print(json.dumps(summary), flush=True)
    if not summary['capture_complete']:
        return 1
    if summary['state'] in {'completed', 'rejected'}:
        return 0
    return 3 if summary['state'] in {'waiting_confirmation', 'waiting_execution'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
