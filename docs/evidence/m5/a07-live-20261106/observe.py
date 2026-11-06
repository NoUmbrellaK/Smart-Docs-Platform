"""One-run A07 fixture observation, never imported by product code."""
from contextlib import contextmanager
from unittest.mock import patch

from benchmark_capture import write_snapshot
from m1_http_test import data, digest
from m3_fixed_inputs_test import FixedInputsE2E


@contextmanager
def observe(output):
    original = FixedInputsE2E.prepare_case

    def prepare(fixture, case):
        prepared = original(fixture, case)
        assert case['id'] == 'A07'
        actor, path = prepared['actor'], prepared['path']
        before = data(actor.request('GET', fixture.base() + '/files'))

        def record():
            # Registered last, so it runs before the loader cancels/cleans the task.
            task = prepared['task']['id']
            probe = {'task_id': task, 'phase': 'before_fixture_cleanup',
                     'state': data(actor.request('GET', path)),
                     'files_before': before,
                     'files_after': data(actor.request('GET', fixture.base() + '/files')),
                     'downloads': [], 'counts': {}, 'business_verdict': None}
            for table in ('archive_plans', 'archive_decisions'):
                probe['counts'][table] = fixture.count(
                    "SELECT COUNT(*) FROM " + table + " WHERE task_id='" + task + "'")
            for ref in ('deployment@2', 'collision@1'):
                source = prepared['mapping'][ref]
                reply = actor.request('GET', fixture.base() + '/files/' + source['file_id'] + '/content')
                probe['downloads'].append({'source': ref, 'file_id': source['file_id'],
                    'version_id': source['version_id'], 'http': reply.status,
                    'expected_sha256': source['sha256'], 'actual_sha256': digest(reply.body)})
            write_snapshot(output, probe)

        fixture.addCleanup(record)
        return prepared

    with patch.object(FixedInputsE2E, 'prepare_case', prepare):
        yield
