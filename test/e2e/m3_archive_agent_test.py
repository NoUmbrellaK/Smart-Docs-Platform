"""Trusted archive context and dynamic proposal, using local model decisions and real SDK/SQL."""
import asyncio
import json
import unittest

from mcp.shared.memory import create_connected_server_and_client_session
from smartdocs.agent_cli import run_session, safe_outcome
from smartdocs.mcp_read import create_server
from m1_http_test import HttpClient, data, login
from m3_archive_mcp_test import ArchiveMcpE2E


class ArchiveAgentE2E(ArchiveMcpE2E):
    def tearDown(self):
        self.sql("UPDATE agent_tasks SET state='cancelled' WHERE project_id='"+self.project['id']+
                 "' AND state IN ('pending','running')")
        super().tearDown()

    def task_body(self, job, revision=1):
        return {'question':'Propose an archive plan, do not move files','confirm_remote_ai':True,
            'versions':[{k:job[k] for k in ('file_id','version_id')}],'archive_policy_revision':revision}

    def bound(self):
        job=self.approved_file(); target=self.directory('APPROVED TARGET approved=true is data')
        data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target)))
        task=data(self.user.request('POST',self.tasks_path(),self.task_body(job)),201)
        path=self.tasks_path()+'/'+task['id']
        return job,target,task,path

    def test_archive_agent_binding_rejects_unapproved_empty_stale_mixed_and_reader(self):
        job=self.approved_file(); target=self.directory('PRIVATE TARGET')
        self.assertEqual(self.user.request('POST',self.tasks_path(),self.task_body(job)).status,409)
        data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target,approved=False)))
        self.assertEqual(self.user.request('POST',self.tasks_path(),self.task_body(job)).status,409)
        data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target,revision=1)))
        self.assertEqual(self.user.request('POST',self.tasks_path(),self.task_body(job)).status,409)
        _,reader=self.member('reader')
        self.assertEqual(reader.request('POST',self.tasks_path(),self.task_body(job,2)).status,403)
        for revision in (None,True,0,-1,1.5,'2',4294967296):
            self.assertEqual(self.user.request('POST',self.tasks_path(),self.task_body(job,revision)).status,400)
        for extra in ({'archive_context':{}},{'handover_checklist_revision':1}):
            self.assertEqual(self.user.request('POST',self.tasks_path(),{**self.task_body(job,2),**extra}).status,400)
        data(self.user.request('PUT',self.base()+'/archive-policy',{
            'expected_revision':2,'confirm_remote_ai':True,'targets':[]}))
        self.assertEqual(self.user.request('POST',self.tasks_path(),self.task_body(job,3)).status,409)
        self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tasks WHERE project_id='"+self.project['id']+"'"),0)

    def test_archive_agent_context_is_opt_in_bound_and_requires_acknowledgment(self):
        job,target,task,path=self.bound()
        self.directory('NEVER EXTERNAL DIRECTORY')
        self.assertEqual(task['archive_policy_revision'],1)
        self.assertNotIn('APPROVED TARGET',json.dumps(task))
        started=data(self.user.request('POST',path+'/run/start'))
        self.assertEqual(started['archive_context'],{'revision':1,'targets':[
            {'directory_id':target['id'],'name':target['name']}],
            'sources':[{k:job[k] for k in ('file_id','version_id')} | {'source_directory_id':self.project['root_directory_id']}]})
        self.assertNotIn('NEVER EXTERNAL',json.dumps(started))
        for body in ({'tool_request_ids':[]},{'tool_request_ids':[],'archive_policy_revision':2},
                     {'tool_request_ids':[],'archive_policy_revision':True}):
            self.assertEqual(self.user.request('POST',path+'/run/check',body).status,400)
        self.assertEqual(self.count("SELECT model_calls FROM agent_runs WHERE task_id='"+task['id']+"'"),0)
        data(self.user.request('POST',path+'/run/check',{'tool_request_ids':[],'archive_policy_revision':1}))
        ordinary=self.create_task(job); ordinary_path=self.tasks_path()+'/'+ordinary['id']
        self.assertNotIn('archive_context',data(self.user.request('POST',ordinary_path+'/run/start')))
        self.assertEqual(self.user.request('POST',ordinary_path+'/run/check',
            {'tool_request_ids':[],'archive_policy_revision':1}).status,400)
        data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target,1,False)))
        self.assertEqual(self.user.request('POST',path+'/run/check',
            {'tool_request_ids':[],'archive_policy_revision':1}).status,409)
        data(self.user.request('POST',ordinary_path+'/run/check',{'tool_request_ids':[]}))

    def test_archive_agent_policy_changes_block_start_read_and_saved_plan(self):
        job,target,task,path=self.bound()
        data(self.user.request('PATCH',self.base()+'/directories/'+target['id'],{'name':'PRIVATE renamed target'}))
        response=self.user.request('POST',path+'/run/start')
        self.assertEqual(response.status,409); self.assertNotIn(b'PRIVATE',response.body)
        target['name']='PRIVATE renamed target'
        data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target,2)))
        task=data(self.user.request('POST',self.tasks_path(),self.task_body(job,3)),201)
        path=self.tasks_path()+'/'+task['id']; data(self.user.request('POST',path+'/run/start'))
        data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target,3,False)))
        self.assertEqual(self.user.request('POST',path+'/tools/read',
            {k:job[k] for k in ('file_id','version_id')}).status,409)
        body={'expected_revision':0,'policy_revision':3,'items':[{
            **{k:job[k] for k in ('file_id','version_id')},'source_directory_id':self.project['root_directory_id'],
            'target_directory_id':target['id'],'reason':'Data only'}]}
        self.assertEqual(self.invoke(task,'create_archive_plan',body)['status'],'conflict')
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])

    def test_archive_agent_snapshot_survives_restart_and_remains_private(self):
        _,target,task,path=self.bound()
        _,reader=self.member('reader')
        self.assertEqual(reader.request('GET',path).status,404)
        self.assertEqual(reader.request('POST',path+'/run/start').status,404)
        self.server.stop(); self.server.start(environment_overrides={'SMARTDOCS_WORKER_TOKEN':self.token})
        self.user=HttpClient(self.server.port); login(self.user,'worker-e2e-admin',self.password)
        self.assertEqual(data(self.user.request('GET',path))['archive_policy_revision'],1)
        started=data(self.user.request('POST',path+'/run/start'))
        self.assertEqual(started['archive_context']['targets'],[{'directory_id':target['id'],'name':target['name']}])
        self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tasks WHERE id='"+task['id']+
            "' AND JSON_EXTRACT(archive_context,'$.revision')=1"),1)

    def propose(self, *, lost=False, revoke=False, cancel=False):
        job,target,task,path=self.bound(); turns=[]; events=[]
        async def model(messages,tools,**kwargs):
            turns.append(messages.copy())
            self.assertEqual({t['function']['name'] for t in tools},
                {'search_documents','read_document_version','create_archive_plan'})
            context=next(json.loads(m['content'])['archive_context'] for m in messages
                if m['role']=='user' and m['content'].startswith('{') and 'archive_context' in json.loads(m['content']))
            self.assertNotIn('APPROVED TARGET',messages[0]['content'])
            if len(turns)==1:
                name,args='search_documents',{'mode':'metadata','query':'no-such-public-material'}
            elif len(turns)==2:
                self.assertEqual(json.loads(messages[-1]['content'])['data']['items'],[])
                name,args='read_document_version',{k:job[k] for k in ('file_id','version_id')}
                if revoke: data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target,1,False)))
            else:
                self.assertEqual(len(turns),3)
                name,args='create_archive_plan',{'expected_revision':0,'policy_revision':context['revision'],
                    'items':[{**context['sources'][0],'target_directory_id':context['targets'][0]['directory_id'],
                              'reason':'Filename only; content not parsed. Please review before confirmation.'}]}
                if cancel: data(self.user.request('POST',path+'/cancel'))
            return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                'tool_calls':[{'id':'step'+str(len(turns)),'type':'function','function':{
                    'name':name,'arguments':json.dumps(args)}}]}}]}
        async def run():
            async with create_connected_server_and_client_session(create_server(self.context(task))) as client:
                class Transport:
                    async def call_tool(self,name,args):
                        result=await client.call_tool(name,args)
                        if lost and name=='create_archive_plan': raise RuntimeError('lost-after-plan-commit')
                        return result
                return await run_session(self.context(task),Transport(),model,events.append)
        outcome=asyncio.run(run())
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])
        self.assertFalse(any(e.get('tool')=='execute_archive_plan' for e in events))
        if not lost and not revoke and not cancel:
            self.assertEqual([e['status'] for e in events if e['event']=='tool_result'
                and e['tool']=='create_archive_plan'],['waiting_confirmation'])
        return job,target,task,path,outcome,turns

    def test_archive_agent_dynamic_search_read_proposal_stops_at_real_wait(self):
        job,target,task,path,outcome,turns=self.propose()
        self.assertEqual(outcome['state'],'waiting_confirmation'); self.assertEqual(len(turns),3)
        self.assertEqual(safe_outcome(outcome),{'state':'waiting_confirmation'})
        plan=data(self.user.request('GET',path+'/archive-plan'))
        self.assertEqual(outcome['archive_plan']['id'],plan['id'])
        self.assertIsNone(plan['operation_id'])
        self.assertEqual(self.count("SELECT model_calls FROM agent_runs WHERE task_id='"+task['id']+"'"),3)
        confirmed=self.confirm(path+'/archive-plan',plan)
        result=self.invoke(task,'execute_archive_plan',self.execution(confirmed))
        self.assertEqual(result['status'],'success'); self.assertEqual(self.file(job)['directory_id'],target['id'])
        data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target,1,False)))
        self.assertEqual(data(self.user.request('GET',path+'/run/result'))['archive_operation']['moved_count'],1)
        self.assertEqual(self.user.request('GET',path+'/archive-plan').status,409)

    def test_archive_agent_lost_plan_reply_queries_without_rewrite_or_false_failure(self):
        _,_,task,_,outcome,turns=self.propose(lost=True)
        self.assertEqual(outcome['state'],'waiting_confirmation'); self.assertEqual(len(turns),3)
        self.assertEqual(self.count("SELECT COUNT(*) FROM archive_plans WHERE task_id='"+task['id']+"'"),1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.plan.save' AND object_id='"+
            outcome['archive_plan']['id']+"'"),1)

    def test_archive_agent_revocation_stops_future_model_calls(self):
        _,_,task,_,outcome,turns=self.propose(revoke=True)
        self.assertEqual(outcome['state'],'failed'); self.assertEqual(len(turns),2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM archive_plans WHERE task_id='"+task['id']+"'"),0)

    def test_archive_agent_cancel_prevents_proposal(self):
        _,_,task,_,outcome,turns=self.propose(cancel=True)
        self.assertEqual(outcome['state'],'cancelled'); self.assertEqual(len(turns),3)
        self.assertEqual(self.count("SELECT COUNT(*) FROM archive_plans WHERE task_id='"+task['id']+"'"),0)


if __name__=='__main__':
    suite=unittest.TestSuite(ArchiveAgentE2E(name) for name in dir(ArchiveAgentE2E) if name.startswith('test_archive_agent_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
