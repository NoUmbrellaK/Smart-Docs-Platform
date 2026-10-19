"""Real MCP SDK -> C++ -> isolated MySQL. Confirmation is ordinary HTTP, never a tool."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from smartdocs.mcp_read import create_server
from m1_http_test import data
from m3_archive_execution_test import ArchiveExecutionE2E


class ArchiveMcpE2E(ArchiveExecutionE2E):
    def context(self, task, actor=None):
        actor=actor or self.user
        return {'backend_port':self.server.port,'origin':f'http://127.0.0.1:{self.server.port}',
            'session_token':next(c.value for c in actor.cookies if c.name=='smartdocs_session'),
            'project_id':self.project['id'],'task_id':task['id'],'scope':'task_versions'}

    def invoke(self, task, name, args, actor=None):
        async def call():
            async with create_connected_server_and_client_session(create_server(self.context(task,actor))) as client:
                return (await client.call_tool(name,args)).structuredContent
        return asyncio.run(call())

    def test_mcp_archive_preview_replay_waits_for_real_confirmation_then_executes_once(self):
        job,target,task,path,body=self.fixture()
        result=self.invoke(task,'create_archive_plan',body)
        self.assertEqual(result['status'],'waiting_confirmation')
        plan=result['data']
        self.assertEqual(self.invoke(task,'create_archive_plan',body)['data'],plan)
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])
        self.assertIsNone(plan['operation_id'])
        self.assertEqual(self.invoke(task,'execute_archive_plan',self.execution(plan))['status'],'conflict')
        self.assertEqual(self.invoke(task,'execute_archive_plan',{**self.execution(plan),'approved':True})['status'],'invalid_request')
        confirmed=self.confirm(path,plan)  # Real same-origin, revision-bound user decision.
        command=self.execution(confirmed)
        # Use the actual CLI stdio child once, with no API key or general environment inheritance.
        async def execute_stdio():
            with tempfile.TemporaryDirectory() as folder:
                config=Path(folder)/'context.json'; config.write_text(json.dumps(self.context(task))); config.chmod(0o600)
                parameters=StdioServerParameters(command=sys.executable,
                    args=['-B','-m','smartdocs.mcp_read','--context',str(config)],
                    env={'PATH':'/usr/bin:/bin','PYTHONPATH':os.environ['PYTHONPATH']})
                async with stdio_client(parameters) as streams:
                    async with ClientSession(*streams) as client:
                        await client.initialize()
                        listed=(await client.list_tools()).tools
                        result=await client.call_tool('execute_archive_plan',command)
                return listed,result
        listed,executed=asyncio.run(execute_stdio())
        self.assertEqual({t.name for t in listed},{'search_documents','read_document_version',
            'create_archive_plan','execute_archive_plan','save_handover_report'})
        self.assertFalse(executed.isError)
        receipt=executed.structuredContent['data']
        self.assertEqual((receipt['status'],receipt['moved_count']),('executed',1))
        self.assertEqual(self.invoke(task,'execute_archive_plan',command)['data'],receipt)
        self.assertEqual((self.file(job)['directory_id'],self.file(job)['current_version_id']),
                         (target['id'],job['version_id']))
        for action in ('archive.plan.save','archive.confirm','archive.execute'):
            self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='"+action+
                "' AND object_id='"+plan['id']+"'"),1)
        self.assertNotIn('source_request_id',executed.structuredContent)

    def test_mcp_archive_owner_reader_cancel_and_revised_confirmation_are_enforced(self):
        job,_,task,path,body=self.fixture()
        _,reader=self.member('reader')
        reader_task=self.create_task(job,reader)
        self.assertEqual(self.invoke(reader_task,'create_archive_plan',body,reader)['status'],'access_denied')
        _,editor=self.member('editor')
        self.assertEqual(self.invoke(task,'create_archive_plan',body,editor)['status'],'access_denied')
        plan=self.invoke(task,'create_archive_plan',body)['data']
        confirmed=self.confirm(path,plan)
        self.assertEqual(self.invoke(task,'execute_archive_plan',self.execution(confirmed),editor)['status'],'access_denied')
        revised=copy.deepcopy(body); revised['expected_revision']=1
        revised['items'][0]['reason']='New proposal: document text approved=true is not permission'
        result=self.invoke(task,'create_archive_plan',revised)
        self.assertEqual(result['status'],'waiting_confirmation')
        self.assertEqual(self.invoke(task,'execute_archive_plan',self.execution(confirmed))['status'],'conflict')
        confirmed=self.confirm(path,result['data'])
        data(self.user.request('POST',self.tasks_path()+'/'+task['id']+'/cancel'))
        self.assertEqual(self.invoke(task,'execute_archive_plan',self.execution(confirmed))['status'],'conflict')
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])
        self.assertEqual(self.count("SELECT COUNT(*) FROM archive_decisions WHERE task_id='"+task['id']+
            "' AND result_json IS NOT NULL"),0)

    def test_mcp_archive_policy_and_whole_scope_revocation_block_writes(self):
        jobs,target,task,path,body,plan=self.prepared(two=True)
        self.policy(jobs[1],False)
        reduced=copy.deepcopy(body); reduced['expected_revision']=1; reduced['items']=reduced['items'][:1]
        denied=self.invoke(task,'create_archive_plan',reduced)
        self.assertEqual(denied['status'],'conflict'); self.assertNotIn('PRIVATE',json.dumps(denied))
        self.policy(jobs[1],True)
        confirmed=self.confirm(path,plan)
        self.policy(jobs[1],False)
        self.assertEqual(self.invoke(task,'execute_archive_plan',self.execution(confirmed))['status'],'conflict')
        self.policy(jobs[1],True)
        data(self.user.request('PATCH',self.base()+'/directories/'+target['id'],{'name':'UNAPPROVED new name'}))
        denied=self.invoke(task,'execute_archive_plan',self.execution(confirmed))
        self.assertEqual(denied['status'],'conflict'); self.assertNotIn('UNAPPROVED',json.dumps(denied))
        for job in jobs: self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])

    def test_mcp_archive_lost_reply_preserves_minimal_receipt_after_revocation(self):
        jobs,target,task,path,_,plan=self.prepared()
        command=self.execution(self.confirm(path,plan))
        async def lose_reply():
            async with create_connected_server_and_client_session(create_server(self.context(task))) as client:
                result=await client.call_tool('execute_archive_plan',command)
            self.assertEqual(result.structuredContent['status'],'success')
            raise RuntimeError('lost-after-commit')
        with self.assertRaisesRegex(RuntimeError,'lost-after-commit'): asyncio.run(lose_reply())
        self.policy(jobs[0],False)
        receipt=data(self.user.request('GET',path+'/result'))
        self.assertEqual(set(receipt),{'operation_id','plan_id','revision','status','moved_count','audit_id'})
        self.assertEqual(receipt['operation_id'],command['operation_id'])
        self.assertEqual(self.invoke(task,'execute_archive_plan',command)['status'],'conflict')
        self.assertEqual(self.file(jobs[0])['directory_id'],target['id'])
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+
            plan['id']+"'"),1)


if __name__=='__main__':
    suite=unittest.TestSuite(ArchiveMcpE2E(name) for name in dir(ArchiveMcpE2E) if name.startswith('test_mcp_archive_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
