"""Real SDK -> C++ -> SQL report path; model decisions are local test inputs."""
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
from smartdocs import worker
from smartdocs.agent_cli import run_session
from smartdocs.mcp_read import create_server
from m1_http_test import data
from m3_handover_report_test import HandoverReportE2E


class ReportMcpE2E(HandoverReportE2E):
    def context(self, task, actor):
        return {'backend_port':self.server.port,'origin':f'http://127.0.0.1:{self.server.port}',
            'session_token':next(c.value for c in actor.cookies if c.name=='smartdocs_session'),
            'project_id':self.project['id'],'task_id':task['id'],'scope':'task_versions'}

    def run_report(self, *, lost=False, cancel=False):
        jobs=[]
        for name in ('deployment.txt','uncited.txt'):
            job=self.upload(name,('Public instructions '+name).encode(),'text/plain'); self.policy(job,True); jobs.append(job)
        self.assertEqual(worker.run_once(self.client),'completed'); self.checklist()
        _,actor=self.member('reader')
        create=self.body(jobs[0]); create['versions']=[{k:j[k] for k in ('file_id','version_id')} for j in jobs]
        task=data(actor.request('POST',self.tasks_path(),create),201); path=self.tasks_path()+'/'+task['id']
        context=self.context(task,actor); events=[]; turns=[]; body=None
        def tool(name,args,ident):
            return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}
        async def model(messages,tools,**kwargs):
            nonlocal body
            turns.append(copy.deepcopy(messages))
            self.assertEqual({t['function']['name'] for t in tools},{'search_documents','read_document_version','save_handover_report'})
            if len(turns)==1:
                calls=[tool('search_documents',{'mode':'metadata','query':'not-existing-unique-phrase'},'search')]
            elif len(turns)==2:
                self.assertEqual(json.loads(messages[-1]['content'])['data']['items'],[])
                calls=[tool('read_document_version',{**create['versions'][0],'range':{}},'read'),
                       tool('read_document_version',create['versions'][1],'meta')]
            else:
                self.assertEqual(len(turns),3)
                payloads=[json.loads(m['content']) for m in messages if m['role']=='tool']
                chunk=next(p['data']['chunks'][0] for p in payloads if p.get('data',{}).get('chunks'))
                operation=next(json.loads(m['content'])['report_operation_id'] for m in messages
                    if m['role']=='user' and m['content'].startswith('{') and 'report_operation_id' in json.loads(m['content']))
                body={'operation_id':operation,'report':{'summary':'Related deployment evidence found',
                    'limitations':'The uncited file was not parsed; not a completeness guarantee',
                    'recommendations':'Review the remaining material manually','items':[{'key':'deployment','status':'found',
                    'notes':'Relevant instructions, not acceptance','citations':[{k:chunk[k] for k in ('file_id','version_id','chunk_id')}]}]}}
                if cancel: data(actor.request('POST',path+'/cancel'))
                calls=[tool('save_handover_report',body,'save')]
            return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':calls}}]}
        async def execute():
            async with create_connected_server_and_client_session(create_server(context)) as client:
                class Transport:
                    async def call_tool(self,name,args):
                        response=await client.call_tool(name,args)
                        if lost and name=='save_handover_report': raise RuntimeError('lost-after-commit')
                        return response
                return await run_session(context,Transport(),model,events.append)
        outcome=asyncio.run(execute())
        self.assertEqual(len(turns),3)
        self.assertNotIn(context['session_token'],json.dumps(turns))
        self.assertNotIn('Public instructions',json.dumps(events))
        return jobs,task,path,body,actor,outcome,events

    def test_mcp_report_reader_dynamic_search_read_save_and_exact_replay(self):
        jobs,task,path,body,actor,outcome,events=self.run_report()
        self.assertEqual((outcome['state'],outcome['kind']),('completed','handover_report'))
        saved=data(actor.request('GET',path+'/handover-report'))
        self.assertEqual(saved['id'],outcome['handover_report']['id'])
        self.assertEqual({s['version_id'] for s in saved['source_versions']},{j['version_id'] for j in jobs})
        self.assertEqual(self.report_count(task),1)
        self.assertEqual([e['tool'] for e in events if e['event']=='tool_attempt'],
            ['search_documents','read_document_version','read_document_version','save_handover_report'])
        async def replay():
            async with create_connected_server_and_client_session(create_server(self.context(task,actor))) as client:
                return await client.call_tool('save_handover_report',body)
        self.assertEqual(asyncio.run(replay()).structuredContent['data'],outcome['handover_report'])
        self.assertEqual(self.report_count(task),1)
        self.policy(jobs[1],False)
        self.assertEqual(asyncio.run(replay()).structuredContent['status'],'conflict')
        self.assertEqual(actor.request('GET',path+'/handover-report/markdown').status,409)

    def test_mcp_report_lost_reply_recovers_committed_result_without_second_save(self):
        _,task,_,_,_,outcome,events=self.run_report(lost=True)
        self.assertEqual((outcome['state'],outcome['kind']),('completed','handover_report'))
        self.assertEqual(sum(e['event']=='tool_attempt' and e['tool']=='save_handover_report' for e in events),1)
        self.assertEqual(self.report_count(task),1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='handover.report.save' AND object_id='"+
            outcome['handover_report']['id']+"'"),1)

    def test_mcp_report_cancel_before_save_cannot_create_report(self):
        _,task,_,_,_,outcome,_=self.run_report(cancel=True)
        self.assertEqual(outcome['state'],'cancelled'); self.assertEqual(self.report_count(task),0)

    def test_mcp_report_invalid_citation_owner_and_operation_are_rejected(self):
        _,task,_,body,actor=self.prepared_report(reader=True)
        async def invoke(context,args):
            async with create_connected_server_and_client_session(create_server(context)) as client:
                return (await client.call_tool('save_handover_report',args)).structuredContent
        context=self.context(task,actor)
        bad=copy.deepcopy(body); bad['report']['items'][0]['citations'][0]['chunk_id']='0'*64
        self.assertEqual(asyncio.run(invoke(context,bad))['status'],'invalid_request')
        bad=copy.deepcopy(body); bad['operation_id']='0'*32
        self.assertEqual(asyncio.run(invoke(context,bad))['status'],'conflict')
        self.assertEqual(asyncio.run(invoke(self.context(task,self.user),body))['status'],'access_denied')
        self.assertEqual(self.report_count(task),0)
        async def stdio_save():
            with tempfile.TemporaryDirectory() as folder:
                config=Path(folder)/'context.json'; config.write_text(json.dumps(context)); config.chmod(0o600)
                parameters=StdioServerParameters(command=sys.executable,
                    args=['-B','-m','smartdocs.mcp_read','--context',str(config)],
                    env={'PATH':'/usr/bin:/bin','PYTHONPATH':os.environ['PYTHONPATH']})
                async with stdio_client(parameters) as streams:
                    async with ClientSession(*streams) as client:
                        await client.initialize()
                        return (await client.call_tool('save_handover_report',body)).structuredContent
        result=asyncio.run(stdio_save())
        self.assertEqual(result['status'],'success'); self.assertNotIn('source_request_id',result)
        self.assertEqual(self.report_count(task),1)


if __name__=='__main__':
    suite=unittest.TestSuite(ReportMcpE2E(name) for name in dir(ReportMcpE2E) if name.startswith('test_mcp_report_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
