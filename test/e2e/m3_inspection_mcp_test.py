"""Real MCP SDK -> C++ -> isolated SQL inspection constraints; model choices are local fixtures."""
import asyncio
import copy
import json
import unittest

from mcp.shared.memory import create_connected_server_and_client_session

from m1_http_test import data
from m3_inspection_constraints_test import InspectionConstraintsE2E
from smartdocs.agent_cli import run_session
from smartdocs.mcp_read import create_server


class InspectionMcpE2E(InspectionConstraintsE2E):
    def context(self, task):
        return {'backend_port':self.server.port,'origin':f'http://127.0.0.1:{self.server.port}',
            'session_token':next(cookie.value for cookie in self.user.cookies if cookie.name=='smartdocs_session'),
            'project_id':self.project['id'],'task_id':task['id'],'scope':'task_versions'}

    def invoke(self, task, name, arguments):
        async def call():
            async with create_connected_server_and_client_session(create_server(self.context(task))) as client:
                return (await client.call_tool(name,arguments)).structuredContent
        return asyncio.run(call())

    def test_inspection_mcp_direct_read_cannot_bypass_authoritative_task_restriction(self):
        marker=b'RESTRICTED-BODY-MARKER'
        job=self.upload('metadata-only.txt',marker,'text/plain'); self.policy(job,True)
        task=self.create_constrained(job)
        search=self.invoke(task,'search_documents',{'mode':'metadata','query':'','page':1,'page_size':10})
        self.assertEqual(search['status'],'success')
        denied=self.invoke(task,'read_document_version',{
            'file_id':job['file_id'],'version_id':job['version_id'],'range':{}})
        self.assertEqual(denied['status'],'access_denied')
        self.assertNotIn('data',denied)
        self.assertNotIn(marker,json.dumps(denied).encode())

    def test_inspection_mcp_metadata_only_handover_saves_uncertain_draft_without_read(self):
        job=self.approved_file(); self.checklist()
        task=self.create_constrained(job,handover_checklist_revision=1)
        context=self.context(task); turns=[]; events=[]

        def tool(name,arguments,ident):
            return {'id':ident,'type':'function','function':{
                'name':name,'arguments':json.dumps(arguments,separators=(',',':'))}}

        async def model(messages,tools,**kwargs):
            turns.append(copy.deepcopy({'messages':messages,'tools':tools}))
            if len(turns)==1:
                return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                    'tool_calls':[tool('search_documents',{
                        'mode':'metadata','query':'','page':1,'page_size':10},'search')]}}]}
            operation=next(json.loads(message['content'])['report_operation_id'] for message in messages
                if message['role']=='user' and message['content'].startswith('{')
                and 'report_operation_id' in json.loads(message['content']))
            body={'operation_id':operation,'report':{
                'summary':'Metadata-only candidate review.',
                'limitations':'Document bodies were not available under this task restriction.',
                'recommendations':'Request a separately authorized content review if needed.',
                'items':[{'key':'deployment','status':'uncertain',
                    'notes':'A candidate name was visible, but its body was not inspected.','citations':[]}]}}
            return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                'tool_calls':[tool('save_handover_report',body,'save')]}}]}

        async def run():
            async with create_connected_server_and_client_session(create_server(context)) as client:
                return await run_session(context,client,model,events.append)

        outcome=asyncio.run(run())
        self.assertEqual((outcome['state'],outcome['kind']),('completed','handover_report'))
        self.assertEqual(len(turns),2)
        names={item['function']['name'] for item in turns[0]['tools']}
        self.assertEqual(names,{'search_documents','save_handover_report'})
        search=next(item['function']['parameters'] for item in turns[0]['tools']
                    if item['function']['name']=='search_documents')
        self.assertEqual(search['required'],['mode','query','page_size'])
        self.assertEqual(search['properties']['mode'],{'const':'metadata'})
        self.assertNotIn('read_document_version',names)
        self.assertFalse(any(event.get('tool')=='read_document_version' for event in events))
        saved=data(self.user.request('GET',self.tasks_path()+'/'+task['id']+'/handover-report'))
        self.assertEqual(saved['report']['items'][0]['status'],'uncertain')
        self.assertEqual(saved['report']['items'][0]['citations'],[])


if __name__=='__main__':
    suite=unittest.TestSuite(InspectionMcpE2E(name) for name in dir(InspectionMcpE2E)
        if name.startswith('test_inspection_mcp_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
