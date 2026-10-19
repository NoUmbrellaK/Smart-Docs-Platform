"""Explicit archive continuation through real CLI/SDK/C++/SQL, without a model or API key."""
import asyncio
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp.shared.memory import create_connected_server_and_client_session
from smartdocs.agent_cli import run_session, safe_outcome
from smartdocs.mcp_read import create_server
from m1_http_test import HttpClient, data, login
from m3_archive_agent_test import ArchiveAgentE2E


class ArchiveResumeE2E(ArchiveAgentE2E):
    def resume(self, task, before=None, lost=False, actor=None):
        self.assertIn('resume_archive',inspect.signature(run_session).parameters,'explicit continuation missing')
        context=self.context(task,actor); events=[]; calls=[]
        async def forbidden_model(**kwargs): self.fail('confirmed execution must not call a model')
        async def run():
            async with create_connected_server_and_client_session(create_server(context)) as client:
                class Transport:
                    async def call_tool(inner,name,args):
                        calls.append((name,args))
                        if before: before()
                        result=await client.call_tool(name,args)
                        if lost: raise RuntimeError('lost-after-execute')
                        return result
                return await run_session(context,Transport(),forbidden_model,events.append,resume_archive=True)
        return asyncio.run(run()),calls,events

    def cli(self, task):
        with tempfile.TemporaryDirectory() as folder:
            context=Path(folder)/'context.json'; audit=Path(folder)/'audit.jsonl'
            context.write_text(json.dumps(self.context(task))); context.chmod(0o600)
            result=subprocess.run([sys.executable,'-B','-m','smartdocs.agent_cli','--context',str(context),
                '--audit',str(audit),'--resume-archive'],env={'PATH':'/usr/bin:/bin','PYTHONPATH':os.environ['PYTHONPATH']},
                capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(result.stderr,'')
            events=[json.loads(line) for line in audit.read_text().splitlines()]
            self.assertFalse(any(e['event'].startswith('model_') for e in events))
            return json.loads(result.stdout),events

    def test_resume_cli_wait_confirm_restart_execute_and_receipt_without_key(self):
        job,target,task,path,_,_=self.propose()
        outcome,events=self.cli(task)
        self.assertEqual(outcome,{'state':'waiting_confirmation'})
        self.assertFalse(any(e['event']=='tool_attempt' for e in events))
        plan=data(self.user.request('GET',path+'/archive-plan'))
        confirmed=self.confirm(path+'/archive-plan',plan)
        self.server.stop(); self.server.start(environment_overrides={'SMARTDOCS_WORKER_TOKEN':self.token})
        self.user=HttpClient(self.server.port); login(self.user,'worker-e2e-admin',self.password)
        outcome,events=self.cli(task)
        self.assertEqual(outcome,{'state':'completed'})
        self.assertEqual([e['tool'] for e in events if e['event']=='tool_attempt'],['execute_archive_plan'])
        self.assertEqual((self.file(job)['directory_id'],self.file(job)['current_version_id']),(target['id'],job['version_id']))
        self.policy(job,False)
        outcome,events=self.cli(task)
        self.assertEqual(outcome,{'state':'completed'})
        self.assertFalse(any(e['event']=='tool_attempt' for e in events))
        receipt=data(self.user.request('GET',path+'/archive-plan/result'))
        self.assertEqual(receipt['operation_id'],confirmed['operation_id'])
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan['id']+"'"),1)
        self.assertEqual(self.count("SELECT model_calls FROM agent_runs WHERE task_id='"+task['id']+"'"),3)

    def test_resume_lost_commit_recovers_minimal_receipt_after_approval_revocation(self):
        job,_,task,path,_,_=self.propose()
        plan=self.confirm(path+'/archive-plan',data(self.user.request('GET',path+'/archive-plan')))
        outcome,calls,_=self.resume(task,lost=True)
        self.assertEqual(outcome['state'],'completed'); self.assertEqual(len(calls),1)
        self.policy(job,False)
        replay,calls,_=self.resume(task)
        self.assertEqual(replay,outcome); self.assertEqual(calls,[])
        self.assertEqual(set(outcome['archive_operation']),{'operation_id','plan_id','revision','status','moved_count','audit_id'})
        self.assertEqual(outcome['archive_operation']['operation_id'],plan['operation_id'])

    def test_resume_does_not_switch_to_new_confirmation_during_execution(self):
        job,_,task,path,_,_=self.propose()
        old=self.confirm(path+'/archive-plan',data(self.user.request('GET',path+'/archive-plan')))
        def revise():
            item={k:old['items'][0][k] for k in ('file_id','version_id','source_directory_id','target_directory_id','reason')}
            item['reason']='New separately confirmed proposal'
            fresh=data(self.user.request('PUT',path+'/archive-plan',{'expected_revision':1,'policy_revision':1,'items':[item]}))
            self.confirm(path+'/archive-plan',fresh)
        outcome,calls,_=self.resume(task,before=revise)
        self.assertEqual(outcome['state'],'waiting_execution'); self.assertEqual(outcome['error_code'],'scope_changed')
        self.assertEqual(safe_outcome(outcome),{'state':'waiting_execution','error_code':'scope_changed'})
        self.assertEqual(calls,[('execute_archive_plan',self.execution(old))])
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+old['id']+"'"),0)
        self.assertEqual(self.resume(task)[0]['archive_operation']['revision'],2)

    def test_resume_cancel_between_preflight_and_tool_blocks_move(self):
        job,_,task,path,_,_=self.propose()
        self.confirm(path+'/archive-plan',data(self.user.request('GET',path+'/archive-plan')))
        outcome,calls,_=self.resume(task,before=lambda:data(self.user.request('POST',path+'/cancel')))
        self.assertEqual(outcome['state'],'cancelled'); self.assertEqual(len(calls),1)
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])
        self.assertEqual(self.resume(task)[1],[])

    def test_resume_scope_revocation_and_other_owner_do_not_move(self):
        job,_,task,path,_,_=self.propose()
        self.confirm(path+'/archive-plan',data(self.user.request('GET',path+'/archive-plan')))
        _,editor=self.member('editor')
        denied,calls,_=self.resume(task,actor=editor)
        self.assertEqual(denied['error_code'],'access_denied'); self.assertEqual(calls,[])
        outcome,calls,_=self.resume(task,before=lambda:self.policy(job,False))
        self.assertEqual(outcome['error_code'],'scope_changed'); self.assertEqual(len(calls),1)
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])

    def test_resume_owner_downgrade_is_rejected_by_authority(self):
        job=self.approved_file(); target=self.directory('Approved target')
        data(self.user.request('PUT',self.base()+'/archive-policy',self.settings(target)))
        uid,editor=self.member('editor')
        task=data(editor.request('POST',self.tasks_path(),self.task_body(job)),201)
        path=self.tasks_path()+'/'+task['id']+'/archive-plan'
        plan=data(editor.request('PUT',path,{'expected_revision':0,'policy_revision':1,'items':[{
            'file_id':job['file_id'],'version_id':job['version_id'],'source_directory_id':self.project['root_directory_id'],
            'target_directory_id':target['id'],'reason':'Public metadata proposal'}]}))
        data(editor.request('POST',path+'/decision',self.decision(plan)))
        data(self.user.request('PUT',self.base()+'/members/'+uid,{'role':'reader'}))
        outcome,calls,_=self.resume(task,actor=editor)
        self.assertEqual(outcome,{'state':'waiting_execution','error_code':'access_denied'})
        self.assertEqual(len(calls),1)
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])

    def test_resume_preflight_conflict_is_reported_without_executing(self):
        job,_,task,path,_,_=self.propose()
        self.confirm(path+'/archive-plan',data(self.user.request('GET',path+'/archive-plan')))
        data(self.user.request('PATCH',self.base()+'/files/'+job['file_id'],{
            'name':'Changed after confirmation.txt','directory_id':self.project['root_directory_id']}))
        outcome,calls,_=self.resume(task)
        self.assertEqual(outcome['error_code'],'scope_changed'); self.assertEqual(calls,[])
        self.assertEqual(self.file(job)['directory_id'],self.project['root_directory_id'])


if __name__=='__main__':
    suite=unittest.TestSuite(ArchiveResumeE2E(name) for name in dir(ArchiveResumeE2E) if name.startswith('test_resume_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
