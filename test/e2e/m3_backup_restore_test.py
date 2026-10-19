"""Paired disposable restore; optional real offline vectors, never a cloud model."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import unittest

from m1_http_test import (HttpClient, ServerProcess, complete, create_upload, data,
                          login, put_part, upload_parts)
from m2_worker_test import ROOT
from m3_handover_report_test import HandoverReportE2E
from smartdocs import search, vector_worker, worker


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            value.update(block)
    return value.hexdigest()


class BackupRestoreE2E(HandoverReportE2E):
    with_vectors = False

    def encode(self, chunks):
        self.encoding_requests += 1
        return vector_worker.request_encoding(os.environ['SMARTDOCS_ENCODER_SOCKET'], chunks)

    def hybrid(self, client):
        result = search.search_documents(
            lambda arguments: data(client.request('POST', self.base() + '/search', arguments)),
            self.encode, {'query': 'Evidence'})
        result['items'].sort(key=lambda hit: hit['chunk']['chunk_id'])
        return result

    def vector_rows(self, database):
        # Read-only fingerprint includes exact vectors, windows, stable chunks and revision.
        rows = self.mysql('SELECT chunk_id,window_ordinal,job_id,embedding_revision,char_start,char_end,vector '
                          'FROM document_embeddings ORDER BY chunk_id,window_ordinal', database)
        self.assertEqual(len(rows.splitlines()), 2)
        return hashlib.sha256(rows.encode()).hexdigest()

    def prepare_vectors(self, jobs):
        self.encoding_requests = 0
        vector = vector_worker.VectorClient(self.server.port, self.token)
        for _ in range(2):
            self.assertEqual(vector_worker.run_once(vector, self.encode), 'completed')
        # The obsolete third version and unapproved replacement must not be encoded.
        self.assertEqual(vector_worker.run_once(vector, self.encode), 'idle')
        self.assertEqual(self.encoding_requests, 2)
        result = self.hybrid(self.user)
        self.assertEqual(result['retrieval'], 'hybrid')
        self.assertEqual({hit['version_id'] for hit in result['items']},
                         {job['version_id'] for job in jobs[:2]})
        self.assertTrue(all('vector' in hit['match_sources'] for hit in result['items']))
        return self.vector_rows('smart_docs_test'), result

    def rebuild_vectors(self, admin, jobs, snapshot):
        rows_hash, expected_search = snapshot
        database = 'smart_docs_restore_test'
        self.assertEqual(self.vector_rows(database), rows_hash)
        self.assertEqual(self.hybrid(admin), expected_search)
        before_attempts = int(self.mysql("SELECT SUM(attempt_count) FROM processing_jobs "
                                        "WHERE task_type='vectorize'", database))
        for job in jobs[:2]:
            # Explicit admin reapproval queues a fresh attempt through the existing API.
            # It is confined to the restored synthetic versions, not a new model consent.
            data(admin.request('PUT', self.base() + '/files/' + job['file_id'] + '/remote-ai-policy',
                {'policy': 'remote_ai_allowed', 'approved_version_ids': [job['version_id']]}))
        degraded = self.hybrid(admin)
        self.assertEqual(degraded['retrieval'], 'keyword_only')
        self.assertIn('vector_index_unavailable', degraded['warnings'])
        self.assertEqual([hit['chunk'] for hit in degraded['items']],
                         [hit['chunk'] for hit in expected_search['items']])
        self.assertEqual(degraded['coverage']['vector_completed_files'], 0)
        self.assertTrue(all('vector' not in hit['match_sources'] for hit in degraded['items']))
        vector = vector_worker.VectorClient(admin.port, self.token)
        def encode_fresh(chunks):
            # Claim removes old windows before actual local inference, so replay cannot pass.
            for chunk in chunks:
                self.assertRegex(chunk['chunk_id'], r'^[0-9a-f]{64}$')
                self.assertEqual(self.mysql("SELECT COUNT(*) FROM document_embeddings WHERE chunk_id='" +
                                            chunk['chunk_id'] + "'", database), '0')
            return self.encode(chunks)
        for _ in range(2):
            self.assertEqual(vector_worker.run_once(vector, encode_fresh), 'completed')
        self.assertEqual(vector_worker.run_once(vector, encode_fresh), 'idle')
        self.assertEqual(int(self.mysql("SELECT SUM(attempt_count) FROM processing_jobs "
                                       "WHERE task_type='vectorize'", database)), before_attempts + 2)
        self.assertEqual(self.vector_rows(database), rows_hash)
        self.assertEqual(self.hybrid(admin), expected_search)
        self.assertEqual(self.encoding_requests, 8)

    def mysql(self, statement, database='smart_docs_test'):
        self.assertIn(database, ('smart_docs_test', 'smart_docs_restore_test'))
        return subprocess.run(['mysql', '--no-defaults', '--protocol=SOCKET',
            '--socket=' + os.environ['SMARTDOCS_MYSQL_SOCKET'], '--user=root',
            '--batch', '--skip-column-names', database, '--execute', statement],
            check=True, capture_output=True, text=True, timeout=30).stdout.strip()

    def dump(self, destination, database='smart_docs_test', data_only=False):
        self.assertIn(database, ('smart_docs_test', 'smart_docs_restore_test'))
        options = ['--no-create-info'] if data_only else []
        with destination.open('xb') as stream:
            os.chmod(destination, 0o600)
            subprocess.run(['mysqldump', '--no-defaults', '--protocol=SOCKET',
                '--socket=' + os.environ['SMARTDOCS_MYSQL_SOCKET'], '--user=root',
                '--single-transaction', '--skip-lock-tables', '--no-tablespaces',
                '--set-gtid-purged=OFF', '--hex-blob', '--skip-comments',
                '--skip-extended-insert', '--order-by-primary', *options, database],
                stdout=stream, stderr=subprocess.PIPE, check=True, timeout=60)

    def storage_manifest(self, directory):
        manifest = {}
        for path in sorted(directory.rglob('*')):
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.stat().st_mode & 0o077, 0)
            if path.is_file():
                manifest[str(path.relative_to(directory))] = [path.stat().st_size, digest(path)]
        return manifest

    def test_backup_restore_paired_snapshot_and_authoritative_http(self):
        jobs, task, task_path, report_body, reader = self.prepared_report(reader=True)
        report_url = task_path + '/handover-report'
        receipt = data(reader.request('PUT', report_url, report_body))
        report = data(reader.request('GET', report_url))
        markdown = reader.request('GET', report_url + '/markdown').body
        citation = report['report']['items'][0]['citations'][0]
        _, editor = self.member('editor')
        beta = data(self.user.request('POST', '/api/v1/projects', {'name': 'Restore beta'}), 201)
        target = self.directory('Restore destination')
        data(self.user.request('PUT', self.base() + '/archive-policy', self.settings(target)))
        # No model was called: prepared_report admits synthetic tool receipts locally.
        for _ in range(2):
            self.assertEqual(worker.run_once(self.client), 'completed')
        old = jobs[2]
        new_bytes = b'Current restored revision'
        version_upload = create_upload(self.user, self.project['id'], target['id'],
            'unseen.txt', new_bytes, 'text/plain', old['file_id'], old['version_id'])
        upload_parts(self.user, self.project['id'], version_upload, new_bytes)
        new = data(complete(self.user, self.project['id'], version_upload['task_id']))
        self.jobs.append(new)
        self.assertEqual(worker.run_once(self.client), 'completed')
        ongoing = self.create_task(jobs[0])
        ongoing_path = self.tasks_path() + '/' + ongoing['id']
        data(self.user.request('POST', ongoing_path + '/run/start'))
        partial_bytes = b'Synthetic resumable upload\n' * 3000
        partial = create_upload(self.user, self.project['id'], target['id'],
            'partial.txt', partial_bytes, 'text/plain')
        data(put_part(self.user, self.project['id'], partial['task_id'], 0,
                      partial_bytes[:partial['chunk_size']]))
        partial_url = self.base() + '/uploads/' + partial['task_id']
        before_partial = data(self.user.request('GET', partial_url))
        vector_snapshot = self.prepare_vectors(jobs) if self.with_vectors else None
        cited_source = data(reader.request('GET', citation['source_url']))
        expected = {path: data(self.user.request('GET', path)) for path in (
            self.base() + '/directories', self.base() + '/files',
            self.base() + '/archive-policy', self.base() + '/handover-checklist')}
        versions = jobs + [new]
        contents = {self.version_path(job): self.user.request(
            'GET', self.version_path(job) + '/content').body for job in versions}
        documents = {self.version_path(job): self.document(job) for job in versions}
        historical_chunk = self.mysql("SELECT id FROM document_chunks WHERE version_id='" + old['version_id'] + "'")
        self.assertRegex(historical_chunk, r'^[0-9a-f]{64}$')
        historical_url = self.version_path(old) + '/chunks/' + historical_chunk
        historical = data(self.user.request('GET', historical_url))
        self.assertFalse(historical['is_current'])
        search_body = {'mode': 'keyword', 'query': 'Evidence'}
        search_before = data(self.user.request('POST', self.base() + '/search', search_body))
        hits = {hit['version_id'] for hit in search_before['items']}
        # ngram natural-language search may also match "revision" with "Evidence".
        # Require the intended evidence and current-only candidates, not substring semantics.
        self.assertTrue({job['version_id'] for job in jobs[:2]} <= hits)
        self.assertTrue(hits <= {job['version_id'] for job in jobs[:2] + [new]})
        self.assertNotIn(old['version_id'], hits)

        # Quiesce the test application and synchronous worker before BOTH backup parts.
        # The original machine MySQL is never contacted/stopped by this test.
        self.server.stop()
        restored = ServerProcess(ROOT / 'bin/server', self.root / 'restore-logs')
        try:
            backup = self.root / 'backup'
            backup.mkdir(mode=0o700)
            self.dump(backup / 'database.sql')
            self.dump(backup / 'rows.sql', data_only=True)
            shutil.copytree(self.root / 'storage', backup / 'storage')
            manifest = self.storage_manifest(backup / 'storage')
            self.assertTrue(any(path.startswith('objects/') for path in manifest))
            self.assertTrue(any(path.startswith('staging/') for path in manifest))
            self.assertEqual(manifest, self.storage_manifest(self.root / 'storage'))
            # CREATE without IF NOT EXISTS: never overwrite an existing restore database.
            self.mysql('CREATE DATABASE smart_docs_restore_test CHARACTER SET utf8mb4 '
                       'COLLATE utf8mb4_0900_ai_ci; GRANT ALL PRIVILEGES ON '
                       "smart_docs_restore_test.* TO 'smart_docs_test'@'localhost'")
            with (backup / 'database.sql').open('rb') as stream:
                subprocess.run(['mysql', '--no-defaults', '--protocol=SOCKET',
                    '--socket=' + os.environ['SMARTDOCS_MYSQL_SOCKET'], '--user=root',
                    'smart_docs_restore_test'], stdin=stream, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, check=True, timeout=60)
            restore_storage = self.root / 'restore-storage'
            shutil.copytree(backup / 'storage', restore_storage)
            self.assertEqual(manifest, self.storage_manifest(restore_storage))
            self.dump(self.root / 'restored-rows.sql', 'smart_docs_restore_test', data_only=True)
            self.assertEqual(digest(backup / 'rows.sql'), digest(self.root / 'restored-rows.sql'))
            self.mysql('ALTER TABLE document_chunks DROP INDEX ft_document_body; '
                       'ALTER TABLE document_chunks ADD FULLTEXT INDEX ft_document_body '
                       '(body) WITH PARSER ngram', 'smart_docs_restore_test')
            restored.start(environment_overrides={
                'SMARTDOCS_MYSQL_DATABASE': 'smart_docs_restore_test',
                'SMARTDOCS_STORAGE_ROOT': str(restore_storage), 'SMARTDOCS_WORKER_TOKEN': self.token})
            admin = HttpClient(restored.port)
            login(admin, 'worker-e2e-admin', self.password)
            actors = []
            for original, role in ((reader, 'reader'), (editor, 'editor')):
                client = HttpClient(restored.port)
                client.cookies = original.cookies  # persisted sessions must still enforce roles
                visible = data(client.request('GET', '/api/v1/me'))['projects']
                self.assertEqual([(p['id'], p['role']) for p in visible], [(self.project['id'], role)])
                self.assertEqual(client.request('GET', '/api/v1/projects/' + beta['id'] + '/files').status, 404)
                actors.append(client)
            restored_reader, restored_editor = actors
            self.assertEqual(admin.request('GET', '/api/v1/health/ready').status, 200)
            for path, value in expected.items():
                self.assertEqual(data(admin.request('GET', path)), value)
            for path, content in contents.items():
                response = admin.request('GET', path + '/content')
                self.assertEqual(response.status, 200)
                self.assertEqual(response.body, content)
                self.assertEqual(data(admin.request('GET', path + '/document')), documents[path])
            self.assertEqual(admin.request('GET', self.version_path(new) + '/remote-ai/document').status, 404)
            search_after = data(admin.request('POST', self.base() + '/search', search_body))
            keyword_order_changed = search_after['items'] != search_before['items']
            # FULLTEXT rebuild may change relevance statistics/order, not source identity.
            # Compare every hit and coverage/warning field without promising identical rank.
            for result in (search_before, search_after):
                result['items'].sort(key=lambda hit: hit['chunk']['chunk_id'])
            self.assertEqual(search_after, search_before)
            self.assertEqual(data(restored_reader.request('GET', report_url)), report)
            self.assertEqual(restored_reader.request('GET', report_url + '/markdown').body, markdown)
            self.assertEqual(data(restored_reader.request('GET', citation['source_url'])), cited_source)
            self.assertEqual(data(admin.request('GET', historical_url)), historical)
            self.assertEqual(admin.request('GET', self.version_path(new) + '/chunks/' + historical_chunk).status, 404)
            self.assertEqual(data(restored_reader.request('PUT', report_url, report_body)), receipt)
            if self.with_vectors:
                self.rebuild_vectors(admin, jobs, vector_snapshot)
                self.assertEqual(data(restored_reader.request('GET', report_url)), report)
            for client in (admin, restored_editor):
                self.assertEqual(client.request('GET', report_url).status, 404)
            self.assertEqual(restored_reader.request('POST', self.base() + '/directories', {
                'parent_id': target['id'], 'name': 'Denied'}).status, 403)
            self.assertEqual(data(admin.request('GET', ongoing_path))['state'], 'interrupted')
            after_partial = data(admin.request('GET', partial_url))
            self.assertEqual(after_partial['confirmed_parts'], before_partial['confirmed_parts'])
            self.assertEqual(after_partial['state'], 'interrupted')
            upload_parts(admin, self.project['id'], partial, partial_bytes, start=1)
            resumed = data(complete(admin, self.project['id'], partial['task_id']))
            replay = data(complete(admin, self.project['id'], partial['task_id']))
            self.assertTrue(replay['reused'])
            for key in ('file_id', 'version_id', 'processing_job_id'):
                self.assertEqual(resumed[key], replay[key])
            downloaded = admin.request('GET', self.version_path(resumed) + '/content')
            self.assertEqual(downloaded.status, 200)
            self.assertEqual(hashlib.sha256(downloaded.body).hexdigest(), hashlib.sha256(partial_bytes).hexdigest())
            data(admin.request('PUT', self.base() + '/files/' + jobs[1]['file_id'] + '/remote-ai-policy',
                {'policy': 'remote_ai_allowed', 'approved_version_ids': []}))
            self.assertEqual(restored_reader.request('GET', report_url).status, 409)
            self.assertEqual(restored_reader.request('GET', report_url + '/markdown').status, 409)
            # Restore-side writes must not change either the backup or original DB/store.
            self.dump(self.root / 'source-after.sql', data_only=True)
            self.assertEqual(digest(backup / 'rows.sql'), digest(self.root / 'source-after.sql'))
            self.assertEqual(manifest, self.storage_manifest(self.root / 'storage'))
            self.assertEqual(manifest, self.storage_manifest(backup / 'storage'))
        finally:
            restored.stop()
            for log in (self.root / 'restore-logs').glob('*.log'):
                self.assertNotIn(self.token, log.read_text(errors='replace'))
            self.server.start(environment_overrides={'SMARTDOCS_WORKER_TOKEN': self.token})
            self.user.port = self.server.port
        reader.port = self.server.port
        self.assertEqual(data(reader.request('GET', report_url)), report)
        print(json.dumps({'case': 'T20-isolated-mixed-restore' if self.with_vectors else 'T20-isolated-keyword-restore', 'result': 'pass',
            'database_rows_sha256': digest(backup / 'rows.sql'), 'storage_files': len(manifest),
            'file_versions': len(versions), 'report_citations': 1, 'resumed_uploads': 1,
            'keyword_order_changed': keyword_order_changed,
            'remote_model_calls': 0, 'local_embedding_requests': self.encoding_requests if self.with_vectors else 0,
            'vector_rebuild': 'pass' if self.with_vectors else 'not_run', 'production_restore': 'not_run'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vectors', action='store_true', help='Require an existing isolated offline encoder socket')
    BackupRestoreE2E.with_vectors = parser.parse_args().vectors
    suite = unittest.TestSuite([BackupRestoreE2E('test_backup_restore_paired_snapshot_and_authoritative_http')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
