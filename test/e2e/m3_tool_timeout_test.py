"""Injected reply timeouts through in-process MCP SDK, real C++ and isolated SQL.

No real model or wall-clock timeout measurement; writes commit before reply loss.
"""
import asyncio
import http.client
import json
import unittest
from unittest.mock import patch

from mcp.shared.memory import create_connected_server_and_client_session
from smartdocs.agent_cli import run_session, safe_outcome
from smartdocs.mcp_read import create_server
from m1_http_test import data
from m3_agent_http_test import tool
from m3_handover_context_test import HandoverContextE2E


class ToolTimeoutE2E(HandoverContextE2E):
    def timeout_case(self, name):
        job=self.approved_file()
        body={'question':'Check synthetic evidence without claiming completeness','confirm_remote_ai':True,
              'versions':[{k:job[k] for k in ('file_id','version_id')}]}
        if name=='save_handover_report':
            self.checklist(); body['handover_checklist_revision']=1
        if name=='create_archive_plan':
            target=self.directory('Approved target')
            data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target)))
            body['archive_policy_revision']=1
        task=data(self.user.request('POST',self.tasks_path(),body),201)
        path=self.tasks_path()+'/'+task['id']
        context={'backend_port':self.server.port,'origin':f'http://127.0.0.1:{self.server.port}',
                 'session_token':next(c.value for c in self.user.cookies if c.name=='smartdocs_session'),
                 'project_id':self.project['id'],'task_id':task['id'],'scope':'task_versions'}
        suffix={'search_documents':'/tools/search','read_document_version':'/tools/read',
                'save_handover_report':'/handover-report','create_archive_plan':'/archive-plan'}[name]
        attempts=[]; model_calls=[]; events=[]
        class TimeoutResponse(http.client.HTTPConnection):
            def request(self, method, url, *args, **kwargs):
                self.lose_reply=method!='GET' and url==path+suffix
                if self.lose_reply: attempts.append((method,url))
                return super().request(method,url,*args,**kwargs)
            def getresponse(self):
                response=super().getresponse()
                if self.lose_reply: raise TimeoutError('private-timeout-detail')
                return response
        async def model(messages, **kwargs):
            model_calls.append(1)
            if name=='search_documents': args={'mode':'metadata','query':''}
            elif name=='read_document_version': args={k:job[k] for k in ('file_id','version_id')} | {'range':{}}
            else:
                contexts=[json.loads(m['content']) for m in messages
                          if m['role']=='user' and m['content'].startswith('{')]
                if name=='create_archive_plan':
                    approved=next(c['archive_context'] for c in contexts if 'archive_context' in c)
                    args={'expected_revision':0,'policy_revision':approved['revision'],'items':[
                        {**approved['sources'][0],'target_directory_id':approved['targets'][0]['directory_id'],
                         'reason':'Metadata only; review before confirming.'}]}
                else:
                    operation=next(c['report_operation_id'] for c in contexts if 'report_operation_id' in c)
                    args={'operation_id':operation,'report':{'summary':'Insufficient evidence',
                        'limitations':'Content not read','recommendations':'Review manually','items':[
                            {'key':'deployment','status':'uncertain','notes':'Content not read','citations':[]}]}}
            return tool(name,args)
        async def run():
            async with create_connected_server_and_client_session(create_server(context)) as client:
                return await run_session(context,client,model,events.append)
        with patch('http.client.HTTPConnection',TimeoutResponse): outcome=asyncio.run(run())
        self.assertEqual(len(attempts),1); self.assertEqual(len(model_calls),1)
        self.assertEqual([e['status'] for e in events if e['event']=='tool_result'],['timeout'])
        self.assertNotIn('private-timeout-detail',json.dumps([outcome,events]))
        saved=data(self.user.request('GET',path+'/run/result'))
        self.assertEqual(saved['model_calls'],1)
        if name in {'search_documents','read_document_version'}:
            self.assertEqual(safe_outcome(outcome),{'state':'failed','error_code':'timeout'})
            self.assertEqual((saved['state'],saved['error_code'],saved['result']),('failed','timeout',None))
            self.assertEqual(self.count("SELECT COUNT(*) FROM agent_runs WHERE task_id='"+task['id']+
                                        "' AND finish_code='timeout'"),1)
        elif name=='save_handover_report':
            self.assertEqual(safe_outcome(outcome),{'state':'completed'})
            self.assertEqual((saved['state'],saved['kind']),('completed','handover_report'))
            self.assertEqual(outcome['handover_report'],saved['handover_report'])
            report=data(self.user.request('GET',path+'/handover-report'))
            self.assertEqual(report['report']['items'][0]['status'],'uncertain')
        else:
            self.assertEqual(safe_outcome(outcome),{'state':'waiting_confirmation'})
            preview=data(self.user.request('GET',path+'/archive-plan'))
            self.assertEqual(preview['state'],'preview'); self.assertIsNone(preview['operation_id'])
            self.assertFalse(preview['executable'])
        for table,expected in (('archive_plans',int(name=='create_archive_plan')),
                               ('handover_reports',int(name=='save_handover_report')),('archive_decisions',0)):
            self.assertEqual(self.count('SELECT COUNT(*) FROM '+table+" WHERE task_id='"+task['id']+"'"),expected)
        calls=data(self.user.request('GET',path+'/tool-calls'))['items']
        self.assertEqual([(c['tool'],c['status']) for c in calls],[(name,'processing' if name=='read_document_version'
                          else 'waiting_confirmation' if name=='create_archive_plan' else 'success')])
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])
        print('TOOL_TIMEOUT '+name+' observed='+outcome['state']+'; one dispatch, no write retry',flush=True)

    def test_timeout_search(self): self.timeout_case('search_documents')
    def test_timeout_read(self): self.timeout_case('read_document_version')
    def test_timeout_report_committed(self): self.timeout_case('save_handover_report')
    def test_timeout_plan_committed(self): self.timeout_case('create_archive_plan')


if __name__=='__main__':
    suite=unittest.TestSuite(ToolTimeoutE2E(name) for name in dir(ToolTimeoutE2E) if name.startswith('test_timeout_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
