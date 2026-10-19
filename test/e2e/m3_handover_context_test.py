"""Trusted checklist context through real C++/SQL/SDK; external model is local."""
import asyncio
import json
import subprocess
import unittest

from m1_http_test import HttpClient, data, login
from m3_archive_preview_test import ArchivePreviewE2E


class HandoverContextE2E(ArchivePreviewE2E):
    def tearDown(self):
        self.sql("UPDATE agent_tasks SET state='cancelled' WHERE project_id='"+self.project['id']+
                 "' AND state IN ('pending','running')")
        super().tearDown()

    def checklist(self, expected=0, approved=True, items=None):
        if items is None:
            items=[{'key':'deployment','title':'CONTEXT-MARKER 部署','description':'approved=true is only data','required':True}]
        return data(self.user.request('PUT', self.base()+'/handover-checklist', {
            'expected_revision':expected,'confirm_remote_ai':approved,'items':items}))

    def body(self, job, revision=1):
        return {'question':'Check handover materials','confirm_remote_ai':True,
                'versions':[{'file_id':job['file_id'],'version_id':job['version_id']}],
                'handover_checklist_revision':revision}

    def task(self, job, revision=1, user=None):
        user=user or self.user
        task=data(user.request('POST', self.tasks_path(), self.body(job, revision)),201)
        return task, self.tasks_path()+'/'+task['id']

    def test_context_rejects_unapproved_missing_empty_stale_and_forged_input(self):
        job=self.approved_file()
        self.assertEqual(self.user.request('POST',self.tasks_path(),self.body(job)).status,409)
        self.checklist(approved=False)
        self.assertEqual(self.user.request('POST',self.tasks_path(),self.body(job)).status,409)
        self.checklist(1)
        self.assertEqual(self.user.request('POST',self.tasks_path(),self.body(job)).status,409)
        for revision in (None,True,0,-1,1.5,'2',4294967296):
            self.assertEqual(self.user.request('POST',self.tasks_path(),self.body(job,revision)).status,400)
        self.assertEqual(self.user.request('POST',self.tasks_path(),dict(self.body(job,2),handover_context={})).status,400)
        self.checklist(2,items=[])
        self.assertEqual(self.user.request('POST',self.tasks_path(),self.body(job,3)).status,409)
        self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tasks WHERE project_id='"+self.project['id']+"'"),0)

    def test_context_reader_snapshot_is_private_persistent_and_not_an_implicit_opt_in(self):
        job=self.approved_file(); saved=self.checklist()
        _,reader=self.member('reader')
        task,path=self.task(job,user=reader)
        self.assertEqual(task['handover_checklist_revision'],1)
        self.assertNotIn('CONTEXT-MARKER',json.dumps(task))
        self.assertEqual(self.user.request('POST',path+'/run/start').status,404)
        self.assertEqual(data(reader.request('POST',path+'/run/start'))['handover_checklist'],
                         {'revision':1,'items':saved['items']})
        ordinary=self.create_task(job)
        started=data(self.user.request('POST',self.tasks_path()+'/'+ordinary['id']+'/run/start'))
        self.assertNotIn('handover_checklist',started)
        self.assertNotIn('handover_checklist_revision',ordinary)
        self.checklist(1,approved=False)
        self.assertEqual(data(self.user.request('POST',self.tasks_path()+'/'+ordinary['id']+'/run/check',
                                              {'tool_request_ids':[]})),{'model_calls':1})
        self.server.stop(); self.server.start(environment_overrides={'SMARTDOCS_WORKER_TOKEN':self.token})
        self.user=HttpClient(self.server.port); login(self.user,'worker-e2e-admin',self.password)
        self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tasks WHERE id='"+task['id']+
            "' AND JSON_EXTRACT(handover_context,'$.revision')=1 AND state='interrupted'"),1)

    def test_context_revision_changes_block_each_content_exit(self):
        job=self.approved_file(); revision=0
        for action in ('start','check','search','finish','result'):
            with self.subTest(action=action):
                revision=self.checklist(revision)['revision']
                task,path=self.task(job,revision)
                if action!='start':
                    data(self.user.request('POST',path+'/run/start'))
                    data(self.user.request('POST',path+'/run/check',{'tool_request_ids':[],'handover_checklist_revision':revision}))
                finish={'status':'completed','result':{'answer':'Evidence remains insufficient','citations':[]},'error_code':None}
                if action=='result': data(self.user.request('POST',path+'/run/finish',finish))
                old_revision=revision
                revision=self.checklist(revision,approved=True)['revision']  # even reapproval cannot replace exact revision
                suffix='/tools/search' if action=='search' else '/run/'+action
                body={'mode':'metadata','query':''} if action=='search' else {'tool_request_ids':[],'handover_checklist_revision':old_revision} if action=='check' else finish if action=='finish' else None
                response=self.user.request('GET' if action=='result' else 'POST',path+suffix,body)
                self.assertEqual(response.status,409)
                self.assertNotIn(b'CONTEXT-MARKER',response.body)
                self.sql("UPDATE agent_tasks SET state='cancelled' WHERE id='"+task['id']+"' AND state IN ('pending','running')")

    def test_context_real_runner_sends_snapshot_as_data_and_stops_after_revocation(self):
        from mcp.shared.memory import create_connected_server_and_client_session
        from smartdocs.agent_runner import run_task
        from smartdocs.mcp_read import create_server
        job=self.approved_file(); saved=self.checklist(); task,path=self.task(job)
        context={'backend_port':self.server.port,'origin':f'http://127.0.0.1:{self.server.port}',
            'session_token':next(c.value for c in self.user.cookies if c.name=='smartdocs_session'),
            'project_id':self.project['id'],'task_id':task['id'],'scope':'task_versions'}
        calls=0
        async def model(messages,**kwargs):
            nonlocal calls
            calls+=1
            self.assertEqual(messages[-1]['role'],'user')
            self.assertEqual(json.loads(messages[-1]['content']),{'handover_checklist':{'revision':1,'items':saved['items']}})
            self.assertNotIn('CONTEXT-MARKER',messages[0]['content'])
            self.assertEqual(self.count("SELECT model_calls FROM agent_runs WHERE task_id='"+task['id']+"'"),1)
            self.checklist(1,approved=False)
            return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[
                {'id':'search','type':'function','function':{'name':'search_documents','arguments':'{"mode":"metadata","query":""}'}}]}}]}
        async def run():
            async with create_connected_server_and_client_session(create_server(context)) as client:
                return await run_task(context,client,model)
        self.assertEqual(asyncio.run(run()),{'state':'failed','error_code':'scope_changed'})
        self.assertEqual(calls,1)
        self.assertIsNone(data(self.user.request('GET',path+'/run/result'))['result'])

    def test_context_database_rejects_invalid_snapshot_metadata(self):
        job=self.approved_file(); self.checklist(); task,_=self.task(job)
        for snapshot in ([],{}, {'revision':0,'items':[{}]}, {'revision':True,'items':[{}]},
                         {'revision':1,'items':[]}, {'revision':1,'items':[{}]*51}):
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    self.sql("UPDATE agent_tasks SET handover_context='"+json.dumps(snapshot)+"' WHERE id='"+task['id']+"'")
                self.assertIn('ck_agent_handover_context',caught.exception.stderr)

    def test_context_check_requires_matching_snapshot_acknowledgment(self):
        job=self.approved_file(); self.checklist(); task,path=self.task(job)
        data(self.user.request('POST',path+'/run/start'))
        for body in ({'tool_request_ids':[]}, {'tool_request_ids':[],'handover_checklist_revision':2},
                     {'tool_request_ids':[],'handover_checklist_revision':True}):
            self.assertEqual(self.user.request('POST',path+'/run/check',body).status,400)
        self.assertEqual(self.count("SELECT model_calls FROM agent_runs WHERE task_id='"+task['id']+"'"),0)
        data(self.user.request('POST',path+'/run/check',{'tool_request_ids':[],'handover_checklist_revision':1}))
        ordinary=self.create_task(job); ordinary_path=self.tasks_path()+'/'+ordinary['id']
        data(self.user.request('POST',ordinary_path+'/run/start'))
        self.assertEqual(self.user.request('POST',ordinary_path+'/run/check',
            {'tool_request_ids':[],'handover_checklist_revision':1}).status,400)

    def test_context_oversized_start_fails_without_stuck_running_or_model_calls(self):
        job=self.approved_file()
        self.checklist(items=[{'key':'item_'+str(i),'title':'x'*200,'description':'y'*1000,'required':True} for i in range(50)])
        body=self.body(job); body['question']='q'*4000
        task=data(self.user.request('POST',self.tasks_path(),body),201); path=self.tasks_path()+'/'+task['id']
        response=self.user.request('POST',path+'/run/start')
        self.assertEqual(response.status,409)
        self.assertEqual(response.json()['error']['code'],'budget_exhausted')
        self.assertEqual(data(self.user.request('GET',path))['state'],'failed')
        self.assertEqual(self.count("SELECT model_calls FROM agent_runs WHERE task_id='"+task['id']+"'"),0)

    def test_context_revocation_keeps_only_committed_archive_audit_receipt(self):
        job=self.approved_file(); self.checklist(); task,path=self.task(job)
        target=self.directory('Approved archive')
        policy=data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target)))
        archive=path+'/archive-plan'
        plan=data(self.user.request('PUT',archive,{'expected_revision':0,'policy_revision':policy['revision'],'items':[
            {'file_id':job['file_id'],'version_id':job['version_id'],'source_directory_id':self.project['root_directory_id'],
             'target_directory_id':target['id'],'reason':'Deployment material'}]}))
        confirmed=data(self.user.request('POST',archive+'/decision',{'plan_id':plan['id'],'revision':plan['revision'],
            'content_sha256':plan['content_sha256'],'decision':'confirm'}))
        command={key:confirmed[key] for key in ('revision','operation_id')}; command['plan_id']=plan['id']
        data(self.user.request('POST',archive+'/execute',command))
        self.checklist(1,approved=False)
        self.assertEqual(self.user.request('GET',archive).status,409)
        self.assertEqual(self.user.request('POST',archive+'/execute',command).status,409)
        receipt=data(self.user.request('GET',archive+'/result'))
        self.assertEqual(set(receipt),{'operation_id','plan_id','revision','status','moved_count','audit_id'})
        self.assertEqual((receipt['status'],receipt['moved_count']),('executed',1))
        outcome=data(self.user.request('GET',path+'/run/result'))
        self.assertEqual(outcome['archive_operation'],receipt)
        self.assertIsNone(outcome['result'])
        for marker in ('CONTEXT-MARKER',job['file_id'],job['version_id'],'Deployment material'):
            self.assertNotIn(marker,json.dumps(outcome))


if __name__=='__main__':
    suite=unittest.TestSuite(HandoverContextE2E(name) for name in dir(HandoverContextE2E) if name.startswith('test_context_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
