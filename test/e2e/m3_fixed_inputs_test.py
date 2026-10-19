"""Materialize 30 frozen fixtures via real HTTP/SQL; NOT a model benchmark.

Only disposable MySQL is allowed by WorkerE2E. No provider/runner is imported.
Preflight runs are cancelled, never confirmed, executed, or counted as successes.
"""
import argparse
import hashlib
import json
import unittest

from m1_http_test import complete, create_upload, data, upload_parts
from m2_worker_test import ROOT
from m3_archive_preview_test import ArchivePreviewE2E
from smartdocs import worker


FIXTURES = ROOT / 'test/fixtures/agent-p0'
CORPUS = json.loads((FIXTURES / 'corpus.json').read_text())
TASKS = json.loads((FIXTURES / 'tasks.json').read_text())['tasks']
VERSIONS = {doc['id'] + '@' + str(version['version']): (doc, version)
            for doc in CORPUS['documents'] for version in doc['versions']}


def pair(job):
    return {key: job[key] for key in ('file_id', 'version_id')}


class FixedInputsE2E(ArchivePreviewE2E):
    def cancel_processing(self, path):
        if data(self.user.request('GET', path))['state'] in ('pending', 'running'):
            data(self.user.request('POST', path + '/cancel'))

    def pending_case(self, case):
        self.assertTrue(callable(getattr(self, 'prepare_case', None)),
                        'fresh pending fixture entrypoint not implemented')
        prepared = self.prepare_case(case)
        self.assertEqual(prepared['admission_http'], {'Q08': 404, 'Q09': 404, 'A09': 403}.get(case['id'], 201))
        task, actor, path = prepared['task'], prepared['actor'], prepared['path']
        if task is not None:
            self.assertEqual(task['state'], 'pending')
            self.assertEqual(data(actor.request('GET', path))['state'], 'pending')
            self.assertCountEqual(task['versions'], [pair(prepared['loaded'][ref]) for ref in case['scope']])
            self.assertEqual(self.user.request('GET', path).status, 404)
        else:
            self.assertIsNone(path)
            self.assertEqual(data(actor.request('GET', self.tasks_path()))['items'], [])
        # Not even a run/start, metadata search or model source admission has happened.
        for table in ('agent_runs', 'agent_tool_calls', 'archive_plans', 'archive_decisions', 'handover_reports'):
            self.assertEqual(self.count('SELECT COUNT(*) FROM ' + table), 0, table)
        if case['id'] == 'H03':
            self.assertEqual(prepared['mapping']['deployment@2']['parse_state'], 'pending')
            self.assertEqual(prepared['mapping']['deployment@2']['chunks'], [])
        if case['id'] == 'H05':
            self.assertEqual(prepared['upload_order'], case['present'])
            self.assertEqual(len(task['versions']), 11)
        print(json.dumps({'case': case['id'], 'pending_setup': 'passed', 'business_task': 'not_run',
            'admission_http': prepared['admission_http'], 'state': task['state'] if task else 'rejected',
            'agent_runs': 0, 'agent_tool_calls': 0, 'model_calls': 0,
            'archive_plans': 0, 'archive_decisions': 0, 'handover_reports': 0}, ensure_ascii=False), flush=True)

    def prepare_case(self, case):
        """Load one frozen case and return its untouched task plus owner client.

        Only the caller may claim/run it. Cleanup cancels unfinished tasks; returned
        clients carry private cookies and must never be logged or sent to a model.
        """
        projects = {'alpha': self.project, 'beta': data(self.user.request(
            'POST', '/api/v1/projects', {'name': 'Synthetic beta ' + case['id']}), 201)}
        directories = {}
        for project_name, project in projects.items():
            directories[project_name] = {}
            for name in CORPUS['directories']:
                directories[project_name][name] = data(self.user.request(
                    'POST', '/api/v1/projects/' + project['id'] + '/directories',
                    {'parent_id': project['root_directory_id'], 'name': name}), 201)
        editor_id, editor = self.member('editor')
        reader_id, reader = self.member('reader')
        self.assertNotEqual(editor_id, reader_id)
        actor = {'editor': editor, 'reader': reader}[case['role']]
        for client in (editor, reader):
            visible = data(client.request('GET', '/api/v1/me'))['projects']
            self.assertEqual([(p['id'], p['role']) for p in visible],
                             [(self.project['id'], 'editor' if client is editor else 'reader')])
            self.assertEqual(client.request('GET', '/api/v1/projects/' +
                                           projects['beta']['id'] + '/files').status, 404)

        # Explicit initial-state exception, not an interpreter for natural-language setup.
        pending = {'deployment@2'} if case['id'] == 'H03' else set()
        document_order = list(dict.fromkeys(VERSIONS[ref][0]['id'] for ref in case['present']))
        ordered = sorted(case['present'], key=lambda ref: (
            ref in pending, document_order.index(VERSIONS[ref][0]['id']), VERSIONS[ref][1]['version']))
        if case['id'] == 'H05':
            self.assertEqual(ordered, case['present'])
        loaded, latest, approvals, mapping = {}, {}, {}, {}
        for ref in ordered:
            doc, version = VERSIONS[ref]
            project = projects[doc['project']]
            base = '/api/v1/projects/' + project['id']
            directory = directories[doc['project']][doc['directory']]['id']
            content = '\n\n'.join(version['paragraphs']).encode('utf-8')
            previous = latest.get(doc['id'])
            upload = create_upload(self.user, project['id'], directory, doc['name'], content,
                                   'text/markdown' if doc['name'].endswith('.md') else 'text/plain',
                                   previous['file_id'] if previous else None,
                                   previous['version_id'] if previous else None)
            upload_parts(self.user, project['id'], upload, content)
            job = data(complete(self.user, project['id'], upload['task_id']))
            job_path = base + '/processing-jobs/' + job['processing_job_id']
            self.addCleanup(self.cancel_processing, job_path)
            version_path = base + '/files/' + job['file_id'] + '/versions/' + job['version_id']
            # New versions start unapproved even if the previous version was approved.
            self.assertEqual(self.user.request('GET', version_path + '/remote-ai/document').status, 404)
            if previous:
                self.assertEqual(job['file_id'], previous['file_id'])
                self.assertNotEqual(job['version_id'], previous['version_id'])
            if ref not in pending:
                self.assertEqual(worker.run_once(self.client), 'completed')
            approved = approvals.setdefault(doc['id'], [])
            if version['approved']:
                approved.append(job['version_id'])
            data(self.user.request('PUT', base + '/files/' + job['file_id'] + '/remote-ai-policy', {
                'policy': 'remote_ai_allowed' if approved else 'internal_only',
                'approved_version_ids': approved}))
            downloaded = self.user.request('GET', version_path + '/content')
            self.assertEqual(downloaded.status, 200)
            self.assertEqual(downloaded.body, content)
            state = data(self.user.request('GET', version_path + '/document'))
            self.assertEqual(state['parse_state'], 'pending' if ref in pending else 'completed')
            self.assertEqual(state['keyword_state'], 'pending' if ref in pending else 'completed')
            chunks = []
            if version['approved']:
                remote = data(self.user.request('POST', version_path + '/remote-ai/read', {}))
                chunks = remote.get('chunks', [])
                if ref in pending:
                    self.assertEqual(chunks, [])
                else:
                    self.assertEqual(len(chunks), 1)
                    self.assertEqual(chunks[0]['text'], content.decode())
                    self.assertEqual((chunks[0]['line_start'], chunks[0]['line_end']),
                                     (1, 2 * len(version['paragraphs']) - 1))
            else:
                self.assertEqual(self.user.request('POST', version_path + '/remote-ai/read', {}).status, 404)
            loaded[ref] = job
            latest[doc['id']] = job
            mapping[ref] = {**pair(job), 'project_id': project['id'], 'directory_id': directory,
                'sha256': hashlib.sha256(content).hexdigest(), 'approved': version['approved'],
                'parse_state': state['parse_state'], 'keyword_state': state['keyword_state'],
                'chunks': [{k: c[k] for k in ('chunk_id', 'line_start', 'line_end')} for c in chunks]}

        for project_name, project in projects.items():
            listed = data(self.user.request('GET', '/api/v1/projects/' + project['id'] + '/files'))['items']
            expected = {latest[VERSIONS[ref][0]['id']]['file_id']: mapping[ref]['directory_id']
                        for ref in ordered if VERSIONS[ref][0]['project'] == project_name}
            self.assertEqual({f['id']: f['directory_id'] for f in listed}, expected)
            self.assertEqual({f['current_version_id'] for f in listed},
                             {j['version_id'] for j in latest.values() if j['file_id'] in expected})

        policy = data(self.user.request('PUT', self.base() + '/archive-policy', {
            'expected_revision': 0, 'confirm_remote_ai': True, 'targets': [
                {'directory_id': d['id'], 'name': d['name']}
                for name, d in directories['alpha'].items() if name != 'inbox']}))
        body = {'question': case['input'], 'confirm_remote_ai': True,
                'versions': [pair(loaded[ref]) for ref in case['scope']]}
        checklist = None
        if case['category'] == 'archive':
            body['archive_policy_revision'] = policy['revision']
        if case['checklist']:
            checklist = data(self.user.request('PUT', self.base() + '/handover-checklist', {
                'expected_revision': 0, 'confirm_remote_ai': True,
                'items': CORPUS['checklists'][case['checklist']]}))
            self.assertEqual(checklist['items'], CORPUS['checklists'][case['checklist']])
            body['handover_checklist_revision'] = checklist['revision']
        response = actor.request('POST', self.tasks_path(), body)
        expected_status = {'Q08': 404, 'Q09': 404, 'A09': 403}.get(case['id'], 201)
        self.assertEqual(response.status, expected_status, response.body)
        task = path = None
        if expected_status == 201:
            task = data(response, 201)
            path = self.tasks_path() + '/' + task['id']
            self.addCleanup(lambda: data(actor.request('POST', path + '/cancel')))
            self.assertCountEqual(task['versions'], body['versions'])
            self.assertEqual(self.user.request('GET', path).status, 404)
        else:
            self.assertEqual(data(actor.request('GET', self.tasks_path()))['items'], [])
        return {'actor': actor, 'task': task, 'path': path, 'admission_http': response.status,
                'body': body, 'checklist': checklist, 'policy': policy, 'loaded': loaded,
                'mapping': mapping, 'upload_order': ordered}

    def preflight(self, case):
        prepared = self.prepare_case(case)
        actor, task, path = (prepared[key] for key in ('actor', 'task', 'path'))
        body, checklist, policy = (prepared[key] for key in ('body', 'checklist', 'policy'))
        loaded, mapping = prepared['loaded'], prepared['mapping']
        metadata_pages = []
        if task is not None:
            started = data(actor.request('POST', path + '/run/start'))
            if case['id'] == 'Q03':
                empty = data(actor.request('POST', path + '/tools/search',
                    {'mode': 'metadata', 'query': 'legacy-guide.md'}))
                self.assertEqual(empty['items'], [])
            if checklist:
                self.assertEqual(started['handover_checklist'],
                                 {'revision': checklist['revision'], 'items': checklist['items']})
            if case['category'] == 'archive':
                self.assertEqual(started['archive_context']['targets'], policy['targets'])
                self.assertCountEqual(started['archive_context']['sources'], [
                    {**pair(loaded[ref]), 'source_directory_id': mapping[ref]['directory_id']}
                    for ref in case['scope']])
            found = []
            for page in range(1, 4):
                result = data(actor.request('POST', path + '/tools/search',
                    {'mode': 'metadata', 'query': '', 'page': page}))
                metadata_pages.append(len(result['items']))
                found.extend(pair(item) for item in result['items'])
                if not result['has_more']:
                    break
            self.assertFalse(result['has_more'])
            self.assertCountEqual(found, body['versions'])
            if case['id'] == 'H05':
                self.assertEqual(metadata_pages, [10, 1])
            self.assertEqual(data(actor.request('GET', path + '/run/result'))['model_calls'], 0)
            data(actor.request('POST', path + '/cancel'))

        # Global zero counts in this disposable suite prove no preflight ran business writes/models.
        self.assertEqual(self.count('SELECT COALESCE(SUM(model_calls),0) FROM agent_runs'), 0)
        for table in ('archive_plans', 'archive_decisions', 'handover_reports'):
            self.assertEqual(self.count('SELECT COUNT(*) FROM ' + table), 0)
        print(json.dumps({'case': case['id'], 'preflight': 'passed', 'business_task': 'not_run',
            'admission_http': prepared['admission_http'], 'model_calls': 0, 'metadata_page_sizes': metadata_pages,
            'upload_order': prepared['upload_order'], 'fixture_mapping': mapping}, ensure_ascii=False), flush=True)


def fixture_test(case, pending=False):
    def test(self):
        if pending:
            self.pending_case(case)
        else:
            self.preflight(case)
    return test


for case in TASKS:
    setattr(FixedInputsE2E, 'test_fixed_' + case['id'], fixture_test(case))
    setattr(FixedInputsE2E, 'test_pending_' + case['id'], fixture_test(case, pending=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pending-only', action='store_true', help='verify untouched pending tasks; never start runs')
    args = parser.parse_args()
    print(json.dumps({'kind': 'fixture_pending_only' if args.pending_only else 'fixture_preflight_only', 'business_tasks_executed': 0,
        'sha256': {name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
                   for name in ('corpus.json', 'tasks.json')}}), flush=True)
    prefix = 'test_pending_' if args.pending_only else 'test_fixed_'
    suite = unittest.TestSuite(FixedInputsE2E(prefix + case['id']) for case in TASKS)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2, failfast=True).run(suite).wasSuccessful())
