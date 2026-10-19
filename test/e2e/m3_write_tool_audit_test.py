"""All five backend tool traces, including committed writes with lost audit completion."""
import subprocess
import unittest

from m1_http_test import data
from m3_archive_execution_test import ArchiveExecutionE2E
from m3_handover_report_test import HandoverReportE2E
from m3_tool_audit_test import ToolAuditE2E
from m2_worker_test import ROOT


class WriteToolAuditE2E(HandoverReportE2E, ArchiveExecutionE2E, ToolAuditE2E):
    def trace(self, path, actor=None):
        return data((actor or self.user).request('GET', path+'/tool-calls'))['items']

    def test_audit_upgrade_preserves_existing_read_history(self):
        # Runs before write fixtures. Recreate only the schema15 audit constraints in this disposable DB.
        _, _, path = self.prepare()
        data(self.user.request('POST', path+'/tools/search', {'mode': 'metadata', 'query': ''}))
        before = self.trace(path)
        self.sql("ALTER TABLE agent_tool_calls DROP CHECK ck_agent_tool_name, DROP CHECK ck_agent_tool_status, "
                 "ADD CONSTRAINT ck_agent_tool_name CHECK (tool IN ('search_documents','read_document_version')), "
                 "ADD CONSTRAINT ck_agent_tool_status CHECK (status IN ('unknown','success','processing','unavailable',"
                 "'invalid_request','access_denied','conflict','busy','timeout','dependency_failed'))")
        self.sql('DELETE FROM schema_migrations WHERE version=16')
        for _ in range(2):
            result = subprocess.run([str(ROOT/'scripts/migrate.sh')], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'schema_version=16')
        self.assertEqual(self.trace(path), before)

    def test_write_audit_archive_waiting_rejection_execution_and_replay(self):
        jobs, target, task, path, body, plan = self.prepared()
        task_path = self.tasks_path()+'/'+task['id']
        rows = self.trace(task_path)
        self.assertEqual([(r['tool'], r['status']) for r in rows], [('create_archive_plan', 'waiting_confirmation')])
        self.assertEqual(rows[0]['parameters'], {'item_count': 1, 'expected_revision': 0, 'policy_revision': 1})
        denied = self.user.request('POST', path+'/execute', self.execution(plan))
        self.assertEqual(denied.status, 409)
        bad = self.user.request('POST', path+'/execute', {'secret': 'DO-NOT-LOG'})
        self.assertEqual(bad.status, 400)
        confirmed = self.confirm(path, plan)
        command = self.execution(confirmed)
        saved = self.user.request('POST', path+'/execute', command)
        receipt = data(saved)
        replay = self.user.request('POST', path+'/execute', command)
        self.assertEqual(data(replay), receipt)
        rows = self.trace(task_path)
        self.assertEqual([r['status'] for r in rows], ['waiting_confirmation', 'conflict', 'invalid_request', 'success', 'success'])
        self.assertEqual([r['request_id'] for r in rows[1:]], [r.json()['request_id'] for r in (denied, bad, saved, replay)])
        self.assertEqual([r['sequence'] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual(rows[-1]['parameters'], {'revision': 1})
        self.assertTrue(all(isinstance(r['duration_ms'], int) and r['duration_ms'] >= 0 for r in rows))
        raw = self.user.request('GET', task_path+'/tool-calls').body
        for secret in (b'DO-NOT-LOG', b'Deployment material', b'PRIVATE-FILENAME', command['operation_id'].encode()):
            self.assertNotIn(secret, raw)
        self.assertEqual(self.file(jobs[0])['directory_id'], target['id'])
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan['id']+"'"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tool_calls WHERE task_id='"+task['id']+"' AND source_json IS NOT NULL"), 0)

    def test_write_audit_report_replay_does_not_become_model_evidence(self):
        _, task, path, body, actor = self.prepared_report(reader=True)
        save = actor.request('PUT', path+'/handover-report', body)
        replay = actor.request('PUT', path+'/handover-report', body)
        self.assertEqual(data(save), data(replay))
        rows = self.trace(path, actor)
        self.assertEqual([r['tool'] for r in rows], ['read_document_version', 'read_document_version', 'save_handover_report', 'save_handover_report'])
        self.assertEqual([r['request_id'] for r in rows[2:]], [save.json()['request_id'], replay.json()['request_id']])
        self.assertEqual(rows[-1]['parameters'], {'item_count': 1})
        self.assertEqual(self.user.request('GET', path+'/tool-calls').status, 404)
        self.assertEqual(self.report_count(task), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tool_calls WHERE task_id='"+task['id']+"' AND tool='save_handover_report' AND (source_json IS NOT NULL OR model_call IS NOT NULL)"), 0)
        self.assertNotIn(b'REPORT-MARKER', actor.request('GET', path+'/tool-calls').body)

    def fault(self, phase):
        self.sql('CREATE TRIGGER write_tool_audit_fault BEFORE '+phase+" ON agent_tool_calls FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic audit fault'")

    def test_write_audit_business_503_is_unknown_and_not_retryable(self):
        _, _, task, path, body = self.fixture()
        self.sql("CREATE TRIGGER business_write_fault BEFORE INSERT ON audit_records FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic business fault'")
        try:
            response = self.user.request('PUT', path, body)
            self.assertEqual(response.status, 503)
            row = self.trace(self.tasks_path()+'/'+task['id'])[-1]
            self.assertEqual((row['status'], row['duration_ms']), ('unknown', None))
            self.assertFalse(response.json()['error']['retryable'])
            self.assertEqual(row['request_id'], response.json()['request_id'])
        finally:
            self.sql('DROP TRIGGER business_write_fault')

    def test_write_audit_admission_failure_prevents_plan_and_execution(self):
        job, _, task, path, body = self.fixture()
        self.fault('INSERT')
        try:
            self.assertEqual(self.user.request('PUT', path, body).status, 503)
            self.assertEqual(self.count("SELECT COUNT(*) FROM archive_plans WHERE task_id='"+task['id']+"'"), 0)
        finally:
            self.sql('DROP TRIGGER write_tool_audit_fault')
        plan = data(self.user.request('PUT', path, body))
        confirmed = self.confirm(path, plan)
        self.fault('INSERT')
        try:
            self.assertEqual(self.user.request('POST', path+'/execute', self.execution(confirmed)).status, 503)
            self.assertEqual(self.file(job)['directory_id'], self.project['root_directory_id'])
            self.assertEqual(data(self.user.request('GET', self.tasks_path()+'/'+task['id']))['state'], 'waiting_execution')
        finally:
            self.sql('DROP TRIGGER write_tool_audit_fault')

    def test_write_audit_completion_failure_preserves_committed_move_and_unknown_trace(self):
        jobs, target, task, path, _, plan = self.prepared()
        command = self.execution(self.confirm(path, plan))
        self.fault('UPDATE')
        try:
            lost = self.user.request('POST', path+'/execute', command)
            self.assertEqual(lost.status, 503)
            self.assertNotIn('data', lost.json())
            row = self.trace(self.tasks_path()+'/'+task['id'])[-1]
            self.assertEqual((row['tool'], row['status'], row['duration_ms']), ('execute_archive_plan', 'unknown', None))
            self.assertEqual(row['request_id'], lost.json()['request_id'])
            self.assertEqual(data(self.user.request('GET', path+'/result'))['operation_id'], command['operation_id'])
            self.assertEqual(self.file(jobs[0])['directory_id'], target['id'])
        finally:
            self.sql('DROP TRIGGER write_tool_audit_fault')
        data(self.user.request('POST', path+'/execute', command))
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan['id']+"'"), 1)

    def test_write_audit_report_admission_and_completion_failures_are_distinct(self):
        _, task, path, body, actor = self.prepared_report()
        self.fault('INSERT')
        try:
            self.assertEqual(actor.request('PUT', path+'/handover-report', body).status, 503)
            self.assertEqual(self.report_count(task), 0)
        finally:
            self.sql('DROP TRIGGER write_tool_audit_fault')
        self.fault('UPDATE')
        try:
            lost = actor.request('PUT', path+'/handover-report', body)
            self.assertEqual(lost.status, 503)
            self.assertEqual(self.report_count(task), 1)
            row = self.trace(path)[-1]
            self.assertEqual((row['status'], row['duration_ms']), ('unknown', None))
            self.assertEqual(row['request_id'], lost.json()['request_id'])
        finally:
            self.sql('DROP TRIGGER write_tool_audit_fault')
        receipt = data(actor.request('PUT', path+'/handover-report', body))
        self.assertEqual(data(actor.request('GET', path+'/handover-report'))['id'], receipt['id'])
        self.assertEqual(self.report_count(task), 1)

    def test_write_audit_capacity_is_bounded_and_never_silently_executes(self):
        job, _, task, path, body = self.fixture()
        task_path = self.tasks_path()+'/'+task['id']
        for _ in range(64):
            self.assertEqual(self.user.request('PUT', path, {}).status, 400)
        response = self.user.request('PUT', path, body)
        self.assertEqual(response.status, 429)
        self.assertEqual(response.json()['error']['code'], 'agent_audit_full')
        self.assertEqual(len(self.trace(task_path)), 64)
        self.assertEqual(self.file(job)['directory_id'], self.project['root_directory_id'])
        self.assertEqual(self.count("SELECT COUNT(*) FROM archive_plans WHERE task_id='"+task['id']+"'"), 0)


if __name__ == '__main__':
    suite = unittest.TestSuite(WriteToolAuditE2E(name) for name in dir(WriteToolAuditE2E)
                              if name.startswith(('test_write_audit_', 'test_audit_')))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
