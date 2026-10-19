"""Fixed 25-case MCP acceptance matrix. No provider or real API key is used.

Twenty cases use a real stdio child: fifteen reach C++/disposable MySQL and
five are rejected by MCP argument validation before HTTP. Five timeout cases
inject failure before HTTP send through the real in-process SDK server; they
test classification/no retry, not wall-clock network timeout performance.
"""
import asyncio
from contextlib import nullcontext
from datetime import timedelta
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from smartdocs import mcp_read, worker
from m1_http_test import complete, create_upload, data, upload_parts
from m3_archive_execution_test import ArchiveExecutionE2E
from m3_handover_report_test import HandoverReportE2E


# Literal expectations fixed before running; do not derive them from the implementation.
CASES = (
    ('01', 'search_documents', 'normal', 'success'),
    ('02', 'search_documents', 'invalid', 'invalid_request'),
    ('03', 'search_documents', 'other_owner', 'access_denied'),
    ('04', 'search_documents', 'new_version', 'conflict'),
    ('05', 'search_documents', 'timeout', 'timeout'),
    ('06', 'read_document_version', 'normal', 'success'),
    ('07', 'read_document_version', 'invalid', 'invalid_request'),
    ('08', 'read_document_version', 'other_owner', 'access_denied'),
    ('09', 'read_document_version', 'new_version', 'conflict'),
    ('10', 'read_document_version', 'timeout', 'timeout'),
    ('11', 'create_archive_plan', 'normal', 'waiting_confirmation'),
    ('12', 'create_archive_plan', 'invalid', 'invalid_request'),
    ('13', 'create_archive_plan', 'other_owner', 'access_denied'),
    ('14', 'create_archive_plan', 'new_version', 'conflict'),
    ('15', 'create_archive_plan', 'timeout', 'timeout'),
    ('16', 'execute_archive_plan', 'normal', 'success'),
    ('17', 'execute_archive_plan', 'invalid', 'invalid_request'),
    ('18', 'execute_archive_plan', 'other_owner', 'access_denied'),
    ('19', 'execute_archive_plan', 'new_version', 'conflict'),
    ('20', 'execute_archive_plan', 'timeout', 'timeout'),
    ('21', 'save_handover_report', 'normal', 'success'),
    ('22', 'save_handover_report', 'invalid', 'invalid_request'),
    ('23', 'save_handover_report', 'other_owner', 'access_denied'),
    ('24', 'save_handover_report', 'new_version', 'conflict'),
    ('25', 'save_handover_report', 'timeout', 'timeout'),
)
TOOLS = {'search_documents', 'read_document_version', 'create_archive_plan',
         'execute_archive_plan', 'save_handover_report'}


class McpContractMatrix(HandoverReportE2E, ArchiveExecutionE2E):
    def fixture_for(self, tool):
        if tool == 'save_handover_report':
            jobs, task, _, arguments, actor = self.prepared_report()
            return jobs[0], task, arguments, actor, None
        if tool == 'execute_archive_plan':
            jobs, target, task, path, _, plan = self.prepared()
            return jobs[0], task, self.execution(self.confirm(path, plan)), self.user, target
        if tool == 'create_archive_plan':
            job, target, task, _, arguments = self.fixture()
            return job, task, arguments, self.user, target
        job = self.approved_file()
        self.assertEqual(worker.run_once(self.client), 'completed')
        task = self.create_task(job)
        arguments = {'mode': 'keyword', 'query': 'SOURCE'} if tool == 'search_documents' else {
            'file_id': job['file_id'], 'version_id': job['version_id'], 'range': {}}
        return job, task, arguments, self.user, None

    def invoke(self, task, actor, tool, arguments, inject_timeout=False):
        context = {'backend_port': self.server.port, 'origin': f'http://127.0.0.1:{self.server.port}',
            'session_token': next(c.value for c in actor.cookies if c.name == 'smartdocs_session'),
            'project_id': self.project['id'], 'task_id': task['id'], 'scope': 'task_versions'}

        async def call(client):
            self.assertEqual({entry.name for entry in (await client.list_tools()).tools}, TOOLS)
            return await client.call_tool(tool, arguments)

        async def run():
            if inject_timeout:
                async with create_connected_server_and_client_session(mcp_read.create_server(context)) as client:
                    return await call(client)
            with tempfile.TemporaryDirectory(dir=self.root) as directory:
                path = Path(directory)/'context.json'
                path.write_text(json.dumps(context)); path.chmod(0o600)
                params = StdioServerParameters(command=sys.executable,
                    args=['-B', '-m', 'smartdocs.mcp_read', '--context', str(path)],
                    env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': os.environ['PYTHONPATH']})
                async with stdio_client(params) as streams:
                    async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=30)) as client:
                        await client.initialize()
                        return await call(client)

        # Failure is injected before send: an unknown-after-send write belongs to A18/lost-reply tests.
        with (patch.object(http.client.HTTPConnection, 'request', side_effect=TimeoutError('DO-NOT-ECHO'))
              if inject_timeout else nullcontext()) as fault:
            reply = asyncio.run(run())
            if inject_timeout: self.assertEqual(fault.call_count, 1, 'timeout must not trigger an automatic retry')
        payload = reply.structuredContent
        self.assertEqual(json.loads(reply.content[0].text), payload)
        self.assertEqual(payload['task_id'], task['id'])
        self.assertRegex(payload['request_id'], r'^[0-9a-f]{32}$')
        self.assertIs(type(payload['duration_ms']), int)
        self.assertGreaterEqual(payload['duration_ms'], 0)
        self.assertNotIn('DO-NOT-ECHO', json.dumps(payload))
        self.assertNotIn(context['session_token'], json.dumps(payload))
        return reply

    def business_snapshot(self, task, job):
        counts = tuple(self.count("SELECT COUNT(*) FROM "+table+" WHERE task_id='"+task['id']+"'")
                       for table in ('archive_plans', 'archive_decisions', 'handover_reports'))
        commits = self.count("SELECT COUNT(*) FROM audit_records WHERE project_id='"+self.project['id']+
            "' AND action IN ('archive.plan.save','archive.execute','handover.report.save')")
        return counts, self.file(job), commits

    def contract_case(self, tool, scenario, expected):
        job, task, arguments, actor, target = self.fixture_for(tool)
        if scenario == 'invalid': arguments = dict(arguments, user_id='DO-NOT-ECHO')
        if scenario == 'other_owner': actor = self.member('reader')[1]
        if scenario == 'new_version':
            content = b'NEW-UNAPPROVED-VERSION'
            upload = create_upload(self.user, self.project['id'], self.project['root_directory_id'],
                self.file(job)['name'], content, 'text/plain', file_id=job['file_id'], observed_version=job['version_id'])
            upload_parts(self.user, self.project['id'], upload, content)
            self.jobs.append(data(complete(self.user, self.project['id'], upload['task_id'])))
        before = self.business_snapshot(task, job)
        audit_before = self.count("SELECT COUNT(*) FROM agent_tool_calls WHERE task_id='"+task['id']+"'")
        reply = self.invoke(task, actor, tool, arguments, scenario == 'timeout')
        payload = reply.structuredContent
        self.assertEqual(payload['status'], expected)
        self.assertEqual(reply.isError, scenario != 'normal')
        if scenario != 'normal':
            self.assertNotIn('data', payload)
            for secret in ('SOURCE-MARKER', 'REPORT-MARKER', 'NEW-UNAPPROVED-VERSION', 'PRIVATE-FILENAME'):
                self.assertNotIn(secret, json.dumps(payload))
            self.assertEqual(self.business_snapshot(task, job), before)
            if scenario in {'invalid', 'other_owner', 'timeout'}:
                self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tool_calls WHERE task_id='"+task['id']+"'"), audit_before)
            return
        value = payload['data']
        if tool == 'search_documents':
            self.assertEqual([item['file_id'] for item in value['items']], [job['file_id']])
            self.assertEqual(value['items'][0]['chunk']['text'], 'SOURCE-MARKER')
        elif tool == 'read_document_version':
            self.assertEqual(value['chunks'][0]['text'], 'SOURCE-MARKER')
            self.assertEqual(value['chunks'][0]['version_id'], job['version_id'])
        elif tool == 'create_archive_plan':
            self.assertEqual(value['task_state'], 'waiting_confirmation')
            self.assertFalse(value['executable']); self.assertIsNone(value['operation_id'])
            self.assertEqual(self.file(job), before[1])
        elif tool == 'execute_archive_plan':
            self.assertEqual(value['moved_count'], 1)
            self.assertEqual(self.file(job)['directory_id'], target['id'])
        else:
            self.assertEqual(self.report_count(task), 1)
            saved = data(actor.request('GET', value['url']))
            source = next(item for item in saved['source_versions'] if item['file_id'] == job['file_id'])
            self.assertEqual(source['version_id'], job['version_id'])
            self.assertEqual(saved['report']['items'][0]['status'], 'found')
        if tool in {'create_archive_plan', 'execute_archive_plan', 'save_handover_report'}:
            committed = self.business_snapshot(task, job)
            replay = self.invoke(task, actor, tool, arguments).structuredContent
            self.assertEqual(replay['status'], expected)
            self.assertEqual(replay['data'], value)
            self.assertNotEqual(replay['request_id'], payload['request_id'])
            self.assertEqual(self.business_snapshot(task, job), committed)


def case_test(tool, scenario, expected):
    def test(self): self.contract_case(tool, scenario, expected)
    return test


for case_id, name, scenario, expected in CASES:
    setattr(McpContractMatrix, f'test_contract_{case_id}_{name}_{scenario}', case_test(name, scenario, expected))

if __name__ == '__main__':
    suite = unittest.TestSuite(McpContractMatrix(name) for name in dir(McpContractMatrix) if name.startswith('test_contract_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
