"""A08 test-only observation of unchanged wire bytes and pre-cleanup SQL/files."""
from contextlib import contextmanager
import hashlib
import json
import os
from unittest.mock import patch

from benchmark_capture import write_snapshot
from m1_http_test import data, digest
from m3_fixed_inputs_test import FixedInputsE2E, VERSIONS, pair
from smartdocs.deepseek import DeepSeekModel


@contextmanager
def observe(directory):
    original_prepare, original_send = FixedInputsE2E.prepare_case, DeepSeekModel._send
    source = None
    text = '\n\n'.join(VERSIONS['injection@1'][1]['paragraphs'])
    sequence = 0

    def prepare(fixture, case):
        nonlocal source
        assert case['id'] == 'A08'
        prepared = original_prepare(fixture, case)
        source = prepared['mapping']['injection@1']
        actor, path = prepared['actor'], prepared['path']
        before = data(actor.request('GET', fixture.base() + '/files'))

        def record():
            task = prepared['task']['id']
            reply = actor.request('GET', fixture.base() + '/files/' + source['file_id'] + '/content')
            write_snapshot(directory / 'probe.json', {
                'task_id': task, 'phase': 'before_fixture_cleanup',
                'state': data(actor.request('GET', path)), 'files_before': before,
                'files_after': data(actor.request('GET', fixture.base() + '/files')),
                'counts': {table: fixture.count("SELECT COUNT(*) FROM " + table +
                          " WHERE task_id='" + task + "'") for table in ('archive_plans','archive_decisions')},
                'download': {'http': reply.status, 'expected_sha256': source['sha256'],
                             'actual_sha256': digest(reply.body)}, 'business_verdict': None})

        fixture.addCleanup(record)
        return prepared

    fd = os.open(directory / 'wire.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        async def sent(model, body):
            nonlocal sequence
            sequence += 1
            chunks = []
            for message in json.loads(body)['messages']:
                if message['role'] == 'tool':
                    result = json.loads(message['content'])
                    if result.get('status') == 'success':
                        payload = result['data']
                        chunks.extend(payload.get('chunks', []))
                        chunks.extend(i['chunk'] for i in payload.get('items', []) if 'chunk' in i)
            row = {'sequence': sequence, 'request_bytes': len(body),
                   'sha256': hashlib.sha256(body).hexdigest(),
                   'injection_body_present': any(pair(c) == pair(source) and c.get('text') == text for c in chunks)}
            output.write(json.dumps(row) + '\n'); output.flush(); os.fsync(output.fileno())
            return await original_send(model, body)

        with patch.object(FixedInputsE2E, 'prepare_case', prepare), patch.object(DeepSeekModel, '_send', sent):
            yield
