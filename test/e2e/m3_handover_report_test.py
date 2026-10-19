"""Private reports against real C++/isolated SQL; never calls a model."""
import copy
import http.client
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from m1_http_test import HttpClient, complete, create_upload, data, login, upload_parts
from m3_handover_context_test import HandoverContextE2E
from smartdocs import worker
from benchmark_review import prepare, summarize


class HandoverReportE2E(HandoverContextE2E):
    def prepared_report(self, reader=False, admit=True, check=True):
        jobs=[]
        for name in ('found.txt','uncited.txt','unseen.txt'):
            job=self.upload(name,('Evidence '+name).encode(),'text/plain'); self.policy(job,True); jobs.append(job)
        self.assertEqual(worker.run_once(self.client),'completed')
        self.checklist()
        actor=self.member('reader')[1] if reader else self.user
        create=self.body(jobs[0]); create['versions']=[{k:j[k] for k in ('file_id','version_id')} for j in jobs]
        task=data(actor.request('POST',self.tasks_path(),create),201); path=self.tasks_path()+'/'+task['id']
        started=data(actor.request('POST',path+'/run/start'))
        self.assertRegex(started.get('report_operation_id',''),r'^[0-9a-f]{32}$')
        read=actor.request('POST',path+'/tools/read',{**create['versions'][0],'range':{}})
        chunk=data(read)['chunks'][0]
        meta=actor.request('POST',path+'/tools/read',create['versions'][1]); data(meta)
        receipts=[read.json()['request_id'],meta.json()['request_id']] if admit else []
        if check:
            data(actor.request('POST',path+'/run/check',{'tool_request_ids':receipts,'handover_checklist_revision':1}))
        report={'summary':'REPORT-MARKER <script>alert(1)</script> [bad](javascript:evil)',
            'limitations':'Only selected and read material; incomplete coverage.',
            'recommendations':'Review unresolved content manually.', 'items':[{
                'key':'deployment','status':'found','notes':'Relevant material, not business acceptance.',
                'citations':[{k:chunk[k] for k in ('file_id','version_id','chunk_id')}]}]}
        return jobs,task,path,{'operation_id':started['report_operation_id'],'report':report},actor

    def report_count(self,task):
        return self.count("SELECT COUNT(*) FROM handover_reports WHERE task_id='"+task['id']+"'")

    def test_report_reader_save_exact_replay_private_read_and_safe_markdown(self):
        jobs,task,path,body,actor=self.prepared_report(reader=True)
        url=path+'/handover-report'
        receipt=data(actor.request('PUT',url,body))
        self.assertEqual(receipt['version'],1); self.assertEqual(receipt['url'],url)
        self.assertEqual(data(actor.request('PUT',url,body)),receipt)
        response=actor.request('GET',url); saved=data(response)
        self.assertEqual(saved['report']['summary'],body['report']['summary'])
        self.assertEqual(saved['state'],'draft'); self.assertEqual(saved['checklist']['revision'],1)
        self.assertEqual({s['file_id'] for s in saved['source_versions']},{j['file_id'] for j in jobs[:2]})
        citation=saved['report']['items'][0]['citations'][0]
        self.assertEqual((citation['file_name'],citation['text'],citation['line_start']),('found.txt','Evidence found.txt',1))
        self.assertIn(jobs[0]['version_id'],citation['source_url'])
        self.assertEqual(data(actor.request('GET',path+'/run/result'))['handover_report'],receipt)
        # Wire-format compatibility only: this synthetic report is NOT an H01 business run.
        snapshot={'case_id':'H01','run_kind':'transport_fixture',
            'metadata':{'code_revision':'test-checkout','recorded_at':'contract-test',
                'environment':'isolated C++/SQL fixture','model':'no model invoked',
                'evidence_path':'test_report_reader_save_exact_replay_private_read_and_safe_markdown'},
            'run_result':data(actor.request('GET',path+'/run/result')),'handover_report':saved}
        review=prepare(snapshot); summary=summarize(snapshot,review)
        self.assertEqual(summary['citations_total'],1)
        self.assertEqual(summary['citations_reviewed'],0)
        self.assertIsNone(summary['business_verdict'])
        self.assertFalse(summary['execution_verified'])
        self.assertEqual(review['citations'][0]['citation'],citation)
        self.assertEqual(self.user.request('GET',url).status,404)
        self.assertEqual(self.user.request('GET',url+'/markdown').status,404)
        md=actor.request('GET',url+'/markdown')
        self.assertEqual(md.status,200); self.assertEqual(md.headers['cache-control'],'no-store')
        self.assertIn('attachment;',md.headers['content-disposition'])
        self.assertIn('text/markdown',md.headers['content-type'])
        self.assertEqual(int(md.headers['content-length']),len(md.body))
        self.assertNotIn(b'<script>',md.body); self.assertNotIn(b'[bad](javascript:',md.body)
        self.assertIn(citation['source_url'].encode(),md.body)
        self.assertIn(task['id'].encode(),md.body)
        sources=md.body.decode().split('## 实际准入来源（包括未引用资料）\n\n')[1].split('\n## 摘要')[0]
        self.assertIn(jobs[1]['version_id'],sources); self.assertNotIn(jobs[2]['version_id'],sources)
        self.assertEqual(self.report_count(task),1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='handover.report.save' AND object_id='"+receipt['id']+"'"),1)

    def test_report_unadmitted_or_forged_citation_cannot_save(self):
        _,task,path,body,actor=self.prepared_report(admit=False)
        url=path+'/handover-report'
        self.assertEqual(actor.request('PUT',url,body).status,400)
        body['report']['items'][0]['citations'][0]['chunk_id']='0'*64
        self.assertEqual(actor.request('PUT',url,body).status,400)
        self.assertEqual(self.report_count(task),0)

    def test_report_uncited_dependency_revocation_blocks_all_content_and_replay(self):
        jobs,task,path,body,actor=self.prepared_report()
        url=path+'/handover-report'; data(actor.request('PUT',url,body))
        self.policy(jobs[2],False)  # chosen but never returned/admitted: not fabricated as a report source
        self.assertEqual(actor.request('GET',url).status,200)
        self.policy(jobs[1],False)  # metadata was admitted, despite not appearing in citations
        for method,suffix,payload in (('GET','',None),('GET','/markdown',None),('PUT','',body)):
            response=actor.request(method,url+suffix,payload)
            self.assertEqual(response.status,409); self.assertNotIn(b'REPORT-MARKER',response.body)
        self.assertEqual(actor.request('GET',path+'/run/result').status,409)
        self.assertEqual(self.report_count(task),1)

    def test_report_checklist_and_membership_changes_restrict_content(self):
        _,_,path,body,actor=self.prepared_report()
        url=path+'/handover-report'; data(actor.request('PUT',url,body))
        self.checklist(1,approved=False)
        self.assertEqual(actor.request('GET',url).status,409)
        self.assertEqual(actor.request('GET',url+'/markdown').status,409)
        self.sql("DELETE FROM project_members WHERE project_id='"+self.project['id']+"'")
        self.assertEqual(actor.request('GET',url).status,404)
        self.assertEqual(actor.request('GET',url+'/markdown').status,404)
        # Restore the fixture owner for processing-job cleanup only.
        self.sql("INSERT INTO project_members(project_id,user_id,role) SELECT '"+self.project['id']+"',id,'admin' FROM users WHERE login_name='worker-e2e-admin'")

    def test_report_invalid_shape_operation_and_cancel_do_not_create_draft(self):
        _,task,path,body,actor=self.prepared_report(); url=path+'/handover-report'
        for mutation in ('operation','empty','missing','extra','duplicate'):
            bad=copy.deepcopy(body)
            if mutation=='operation': bad['operation_id']='0'*32
            elif mutation=='empty': bad['report']['items'][0]['citations']=[]
            elif mutation=='missing': bad['report']['items']=[]
            elif mutation=='extra': bad['report']['approved']=True
            else: bad['report']['items']*=2
            self.assertEqual(actor.request('PUT',url,bad).status,409 if mutation=='operation' else 400)
        self.assertEqual(actor.request('PUT',url,body,{'Origin':'https://foreign.invalid'}).status,403)
        self.assertEqual(actor.request('GET',url+'?user_id=x').status,400)
        self.assertEqual(actor.request('PUT',url,dict(body,padding='x'*65536)).status,413)
        data(actor.request('POST',path+'/cancel'))
        self.assertEqual(actor.request('PUT',url,body).status,409)
        self.assertEqual(self.report_count(task),0)

    def test_report_audit_failure_rolls_back_report_and_task_then_retry_succeeds(self):
        _,task,path,body,actor=self.prepared_report(); url=path+'/handover-report'
        self.sql("CREATE TRIGGER report_audit_failure BEFORE INSERT ON audit_records FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test fault'")
        try:
            self.assertEqual(actor.request('PUT',url,body).status,503)
            self.assertEqual(self.report_count(task),0)
            self.assertEqual(data(actor.request('GET',path))['state'],'running')
        finally: self.sql('DROP TRIGGER report_audit_failure')
        data(actor.request('PUT',url,body)); self.assertEqual(self.report_count(task),1)

    def test_report_concurrent_replay_and_changed_body_do_not_duplicate(self):
        _,task,path,body,actor=self.prepared_report(); url=path+'/handover-report'
        barrier=threading.Barrier(2); cookie=actor._cookie_header()
        def save(_):
            barrier.wait(timeout=5)
            return data(HttpClient(self.server.port).request('PUT',url,body,{'Cookie':cookie}))
        with ThreadPoolExecutor(max_workers=2) as pool: receipts=list(pool.map(save,range(2)))
        self.assertEqual(receipts[0],receipts[1]); self.assertEqual(self.report_count(task),1)
        changed=copy.deepcopy(body); changed['report']['summary']='different'
        self.assertEqual(actor.request('PUT',url,changed).status,409)

    def test_report_lost_body_restart_and_expired_run_replays_original(self):
        _,task,path,body,actor=self.prepared_report(); url=path+'/handover-report'
        connection=http.client.HTTPConnection('127.0.0.1',self.server.port,timeout=10)
        connection.request('PUT',url,json.dumps(body),{'Cookie':actor._cookie_header(),
            'Origin':f'http://127.0.0.1:{self.server.port}','Content-Type':'application/json'})
        self.assertEqual(connection.getresponse().status,200); connection.close()
        self.server.stop(); self.server.start(environment_overrides={'SMARTDOCS_WORKER_TOKEN':self.token})
        self.user=HttpClient(self.server.port); login(self.user,'worker-e2e-admin',self.password)
        self.sql("UPDATE agent_runs SET started_at=UTC_TIMESTAMP(6)-INTERVAL 1 HOUR WHERE task_id='"+task['id']+"'")
        receipt=data(self.user.request('PUT',url,body))
        self.assertEqual(data(self.user.request('GET',url))['id'],receipt['id'])
        self.assertEqual(self.report_count(task),1)

    def test_report_empty_evidence_is_explicitly_uncertain_not_found(self):
        _,_,path,body,actor=self.prepared_report(admit=False)
        item=body['report']['items'][0]; item['status']='uncertain'; item['citations']=[]
        item['notes']='No admitted evidence; no conclusion can be drawn.'
        data(actor.request('PUT',path+'/handover-report',body))
        saved=data(actor.request('GET',path+'/handover-report'))
        self.assertEqual(saved['source_versions'],[])
        self.assertEqual(saved['report']['items'][0]['status'],'uncertain')

    def test_report_no_model_admission_and_expired_execution_cannot_save(self):
        _,task,path,body,actor=self.prepared_report(check=False)
        self.assertEqual(actor.request('PUT',path+'/handover-report',body).status,409)
        data(actor.request('POST',path+'/run/check',{'tool_request_ids':[],'handover_checklist_revision':1}))
        self.sql("UPDATE agent_runs SET started_at=UTC_TIMESTAMP(6)-INTERVAL 1 HOUR WHERE task_id='"+task['id']+"'")
        self.assertEqual(actor.request('PUT',path+'/handover-report',body).status,409)
        self.assertEqual(self.report_count(task),0)

    def test_report_dependency_change_before_save_and_soft_delete_after_save(self):
        jobs,task,path,body,actor=self.prepared_report(); url=path+'/handover-report'
        self.policy(jobs[1],False)
        self.assertEqual(actor.request('PUT',url,body).status,409)
        self.assertEqual(self.report_count(task),0)
        self.policy(jobs[1],True)
        data(actor.request('PUT',url,body))
        self.assertEqual(self.user.request('DELETE',self.base()+'/files/'+jobs[1]['file_id']).status,204)
        try:
            for suffix in ('','/markdown'):
                response=actor.request('GET',url+suffix)
                self.assertEqual(response.status,409); self.assertNotIn(b'REPORT-MARKER',response.body)
        finally:
            data(self.user.request('POST',self.base()+'/files/'+jobs[1]['file_id']+'/restore'))

    def test_report_interrupted_task_cannot_first_save(self):
        _,task,path,body,_=self.prepared_report()
        self.server.stop(); self.server.start(environment_overrides={'SMARTDOCS_WORKER_TOKEN':self.token})
        self.user=HttpClient(self.server.port); login(self.user,'worker-e2e-admin',self.password)
        self.assertEqual(data(self.user.request('GET',path))['state'],'interrupted')
        self.assertEqual(self.user.request('PUT',path+'/handover-report',body).status,409)
        self.assertEqual(self.report_count(task),0)

    def test_report_missing_fixed_chunk_blocks_saved_content(self):
        _,_,path,body,actor=self.prepared_report(); url=path+'/handover-report'
        data(actor.request('PUT',url,body))
        chunk=body['report']['items'][0]['citations'][0]['chunk_id']
        self.sql("DELETE FROM document_chunks WHERE id='"+chunk+"'")
        self.assertEqual(actor.request('GET',url).status,409)
        self.assertEqual(actor.request('GET',url+'/markdown').status,409)

    def test_report_new_version_does_not_silently_replace_fixed_source(self):
        jobs,_,path,body,actor=self.prepared_report(); url=path+'/handover-report'
        data(actor.request('PUT',url,body))
        old=jobs[1]; content=b'New material is not the original source'
        upload=create_upload(self.user,self.project['id'],self.project['root_directory_id'],
            'uncited.txt',content,'text/plain',file_id=old['file_id'],observed_version=old['version_id'])
        upload_parts(self.user,self.project['id'],upload,content)
        new=data(complete(self.user,self.project['id'],upload['task_id'])); self.jobs.append(new)
        self.policy(new,True)
        self.assertEqual(actor.request('GET',url).status,409)
        self.assertEqual(actor.request('GET',url+'/markdown').status,409)
        self.assertEqual(actor.request('PUT',url,body).status,409)


if __name__=='__main__':
    suite=unittest.TestSuite(HandoverReportE2E(name) for name in dir(HandoverReportE2E) if name.startswith('test_report_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
