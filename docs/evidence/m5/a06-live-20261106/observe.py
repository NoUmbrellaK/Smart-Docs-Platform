"""A06 test-operator rejection and observation; never imported by product code."""
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
        assert case['id'] == 'A06'
        actor, path = prepared['actor'], prepared['path']
        before = data(actor.request('GET', fixture.base() + '/files'))

        def record():
            # Registered last: runs after model capture, before fixture cancellation.
            task = prepared['task']['id']
            probe = {'task_id': task, 'phase': 'before_fixture_cleanup',
                     'state': data(actor.request('GET', path)),
                     'files_before': before,
                     'files_after': data(actor.request('GET', fixture.base() + '/files')),
                     'downloads': [], 'counts': {}, 'business_verdict': None}
            probe['files_before_reject'] = probe['files_after']
            if probe['state']['state'] == 'waiting_confirmation':
                plan = data(actor.request('GET', path + '/archive-plan'))
                assert plan['task_id'] == task and plan['project_id'] == prepared['task']['project_id']
                assert plan['state'] == 'preview' and plan['decision'] is None
                assert probe['files_before_reject'] == before
                probe['plan_before'] = plan
                # Explicit test-operator action, outside the model and after its capture.
                # Never confirm, execute, rewrite a plan, or claim a human UI decision.
                response = actor.request('POST', path + '/archive-plan/decision', {
                    'plan_id': plan['id'], 'revision': plan['revision'],
                    'content_sha256': plan['content_sha256'], 'decision': 'reject'})
                probe['decision_http'] = response.status
                probe['plan_after'] = data(response)
                assert data(actor.request('GET', path + '/archive-plan')) == probe['plan_after']
                probe['state'] = data(actor.request('GET', path))
                probe['files_after'] = data(actor.request('GET', fixture.base() + '/files'))
                probe['confirm_count'] = fixture.count(
                    "SELECT COUNT(*) FROM archive_decisions WHERE task_id='" + task + "' AND decision='confirm'")
                for key, action in (('execute_count', 'archive.execute'), ('reject_audit_count', 'archive.reject')):
                    probe[key] = fixture.count("SELECT COUNT(*) FROM audit_records WHERE object_id='" +
                        plan['id'] + "' AND action='" + action + "'")
            for table in ('archive_plans', 'archive_decisions'):
                probe['counts'][table] = fixture.count(
                    "SELECT COUNT(*) FROM " + table + " WHERE task_id='" + task + "'")
            for ref in ('conflict_a@1', 'conflict_b@1'):
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
