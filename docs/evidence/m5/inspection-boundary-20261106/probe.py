"""Temporary characterization of missing task inspection enforcement, NOT desired behavior or a model benchmark."""
import copy
import json
import unittest
from m1_http_test import data
from m3_fixed_inputs_test import FixedInputsE2E, TASKS

class InspectionBoundaryProbe(FixedInputsE2E):
    def runTest(self):
        case=next(c for c in TASKS if c['id']=='H05')
        prepared=self.prepare_case(case)
        actor,path=prepared['actor'],prepared['path']
        started=data(actor.request('POST',path+'/run/start'))
        self.assertEqual(started['question'],case['input'])
        first=data(actor.request('POST',path+'/tools/search',
            {'mode':'metadata','query':'','page':1,'page_size':10}))
        self.assertEqual(len(first['items']),10)
        self.assertTrue(first['has_more'])
        self.assertFalse(any('chunk' in item or 'chunks' in item for item in first['items']))
        second=data(actor.request('POST',path+'/tools/search',
            {'mode':'metadata','query':'','page':2,'page_size':10}))
        self.assertEqual(len(second['items']),1)
        ref={k:first['items'][0][k] for k in ('file_id','version_id')}
        body=data(actor.request('POST',path+'/tools/read',{**ref,'range':{}}))
        self.assertEqual(len(body['chunks']),1)
        self.assertTrue(body['chunks'][0]['text'])
        search=data(actor.request('POST',path+'/tools/search',{'mode':'keyword','query':'ORBIT'}))
        self.assertTrue(search['items'])
        self.assertTrue(any(item.get('chunk',{}).get('text') for item in search['items']))
        self.assertEqual(self.user.request('POST',path+'/tools/read',{**ref,'range':{}}).status,404)
        proposed=copy.deepcopy(prepared['body'])
        proposed['inspection_constraints']={'mode':'metadata','max_page':1,'page_size':10}
        rejected=actor.request('POST',self.tasks_path(),proposed)
        self.assertEqual(rejected.status,400)
        self.assertEqual(rejected.json()['error']['code'],'invalid_request')
        self.assertEqual(self.count('SELECT COUNT(*) FROM handover_reports'),0)
        self.assertEqual(self.count('SELECT COUNT(*) FROM archive_decisions'),0)
        calls=data(actor.request('GET',path+'/tool-calls'))['items']
        self.assertEqual([c['status'] for c in calls],['success']*4)
        print(json.dumps({'case_id':'H05','kind':'offline_characterization_not_acceptance',
            'frozen_question_preserved':True,'metadata_page1_items':10,'metadata_page2_items':1,
            'body_read_succeeded':True,'keyword_content_search_succeeded':True,
            'other_task_owner_http':404,'proposed_constraints_field_http':400,
            'reports':0,'archive_decisions':0,'model_requests':0}),flush=True)

if __name__=='__main__':
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite([InspectionBoundaryProbe('runTest')])).wasSuccessful())
