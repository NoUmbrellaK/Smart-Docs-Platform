"""Validate fixed input/oracle references, not model quality or business acceptance."""
from collections import Counter
import json
from pathlib import Path
import unittest


ROOT=Path(__file__).resolve().parents[1]/'fixtures'/'agent-p0'


class AgentBenchmarkFixtures(unittest.TestCase):
    def setUp(self):
        self.corpus=json.loads((ROOT/'corpus.json').read_text())
        self.manifest=json.loads((ROOT/'tasks.json').read_text())
        self.versions={}
        for document in self.corpus['documents']:
            for version in document['versions']:
                key=document['id']+'@'+str(version['version'])
                self.assertNotIn(key,self.versions)
                self.versions[key]=(document,version)

    def test_fixed_denominator_and_unique_task_inputs(self):
        tasks=self.manifest['tasks']
        self.assertEqual(Counter(t['category'] for t in tasks),{'query':10,'archive':10,'handover':10})
        self.assertEqual(len({t['id'] for t in tasks}),30)
        self.assertEqual(len({t['input'] for t in tasks}),30)
        self.assertEqual(self.manifest['dataset'],self.corpus['dataset'])
        self.assertEqual(self.manifest['run_status'],'not_run')

    def test_sources_have_real_literal_paragraphs_and_no_duplicate_versions(self):
        documents=self.corpus['documents']
        self.assertEqual(len({d['id'] for d in documents}),len(documents))
        for doc,version in self.versions.values():
            self.assertIn(doc['project'],self.corpus['projects'])
            self.assertIn(doc['directory'],self.corpus['directories'])
            self.assertIs(type(version['approved']),bool)
            self.assertGreater(version['version'],0)
            self.assertTrue(version['paragraphs'])
            self.assertTrue(all(isinstance(p,str) and p.strip() and '\n' not in p for p in version['paragraphs']))

    def test_scope_and_oracle_anchors_resolve_without_cross_project_leaks(self):
        for task in self.manifest['tasks']:
            with self.subTest(task=task['id']):
                self.assertIn(task['role'],{'reader','editor','admin'})
                self.assertTrue(task['scope']); self.assertLessEqual(set(task['scope']),set(task['present']))
                self.assertEqual(len(set(task['present'])),len(task['present']))
                for source in task['present']: self.assertIn(source,self.versions)
                for source in task['scope']:
                    self.assertEqual(self.versions[source][0]['project'],task['project'])
                expected=task['expected']
                self.assertTrue(expected['required']); self.assertTrue(expected['forbidden'])
                self.assertTrue(expected['allowed_variants'])
                for anchor in expected['evidence']:
                    self.assertIn(anchor['source'],task['scope'])
                    paragraphs=self.versions[anchor['source']][1]['paragraphs']
                    self.assertIs(type(anchor['paragraph']),int)
                    self.assertGreaterEqual(anchor['paragraph'],1)
                    self.assertLessEqual(anchor['paragraph'],len(paragraphs))
                    self.assertTrue(self.versions[anchor['source']][1]['approved'])

    def test_handover_keys_and_archive_targets_use_declared_configuration(self):
        for task in self.manifest['tasks']:
            with self.subTest(task=task['id']):
                if task['category']=='handover':
                    checklist=self.corpus['checklists'][task['checklist']]
                    self.assertEqual(set(task['expected']['statuses']),{item['key'] for item in checklist})
                    self.assertTrue(all(item['title'] and item['description'] and type(item['required']) is bool
                                        for item in checklist))
                    self.assertLessEqual(set(task['expected']['statuses'].values()),{'found','missing','uncertain'})
                if task['category']=='archive':
                    targets=task['expected']['targets']
                    self.assertEqual(set(targets),set() if task['role']=='reader' else set(task['scope']))
                    for choices in targets.values():
                        self.assertTrue(choices)
                        self.assertLessEqual(set(choices),{None,'requirements','design','test','deploy'})


if __name__=='__main__': unittest.main()
