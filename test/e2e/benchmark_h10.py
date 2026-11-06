"""H10 test-only interleaving, never enabled by the product."""
import hashlib
import json
import os
from unittest.mock import patch

from benchmark_capture import write_snapshot
from benchmark_execute import execute
from benchmark_wire import strings
from m3_fixed_inputs_test import VERSIONS, pair
from smartdocs.deepseek import DeepSeekModel


async def execute_h10(fixture, prepared, metadata, directory, **options):
    """Observe unchanged requests, retain the original draft, then revoke only design@1.

    The local fixture administrator is the operator for this frozen test setup.
    This is not archive consent. Never rewrite model output, retry, or regenerate.
    A successful provider response plus matching wire/audit evidence is required;
    merely reading a body locally or handing bytes to _send is insufficient.
    """
    refs = ('requirements@1', 'design@1')
    sources = {ref: pair(prepared['loaded'][ref]) for ref in refs}
    texts = {ref: '\n\n'.join(VERSIONS[ref][1]['paragraphs']) for ref in refs}
    observations = []
    original = DeepSeekModel._send
    descriptor = os.open(directory / 'h10-wire.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        async def observed(model, body):
            chunks = []
            for message in json.loads(body)['messages']:
                if message['role'] == 'tool':
                    result = json.loads(message['content'])
                    if result.get('status') == 'success':
                        payload = result['data']
                        chunks.extend(payload.get('chunks', []))
                        chunks.extend(i['chunk'] for i in payload.get('items', []) if 'chunk' in i)
            row = {'sequence': len(observations) + 1, 'request_bytes': len(body),
                   'sha256': hashlib.sha256(body).hexdigest(),
                   'body_sources': [ref for ref in refs if any(
                       pair(c) == sources[ref] and c.get('text') == texts[ref] for c in chunks)]}
            output.write(json.dumps(row) + '\n')
            output.flush()
            os.fsync(output.fileno())  # Failed evidence persistence must stop before sending.
            observations.append(row)
            return await original(model, body)

        with patch.object(DeepSeekModel, '_send', observed):
            snapshot = await execute('H10', prepared, metadata, directory / 'initial', **options)

    trace = [json.loads(line) for line in (directory / 'initial/audit.jsonl').read_text().splitlines()]
    attempts = [e for e in trace if e['event'] == 'model_attempt']
    results = [e for e in trace if e['event'] == 'model_result']
    wire_matched = (len(observations) == len(attempts) == len(results)
                    and all((w['sequence'], w['request_bytes'], a['request_id']) ==
                            (a['sequence'], a['request_bytes'], r['request_id'])
                            and w['sequence'] == r['sequence']
                            for w, a, r in zip(observations, attempts, results)))
    sent = {ref for w, r in zip(observations, results)
            if wire_matched and r['reason'] == 'success' and r.get('usage')
            for ref in w['body_sources']}
    report = snapshot.get('handover_report', {})
    body = report.get('report', {})
    citations = [c for item in body.get('items', []) for c in item['citations']]
    probe = {'case_id': 'H10', 'state': 'preconditions_not_met', 'business_verdict': None,
             'preconditions': {
                 'captured_draft': snapshot['capture']['complete'] and report.get('state') == 'draft'
                     and (snapshot.get('run_result') or {}).get('state') == 'completed',
                 'wire_audit_matched': wire_matched,
                 'requirements_body_sent': 'requirements@1' in sent,
                 'design_body_sent': 'design@1' in sent,
                 'requirements_cited': any(pair(c) == sources['requirements@1'] for c in citations),
                 'design_uncited': not any(pair(c) == sources['design@1'] for c in citations)},
             'before_http': [], 'after': [], 'revoked': False}
    if all(probe['preconditions'].values()):
        actor, path = prepared['actor'], prepared['path']
        urls = [path + '/handover-report', path + '/handover-report/markdown', path + '/run/result']
        probe['before_http'] = [actor.request('GET', url).status for url in urls]
        task_id = prepared['task']['id']  # execute validated this server-issued identifier.
        count_sql = "SELECT COUNT(*) FROM handover_reports WHERE task_id='" + task_id + "'"
        probe['reports_before'] = fixture.count(count_sql)
        probe['design_in_dependencies'] = sources['design@1'] in report['source_versions']
        if probe['before_http'] == [200, 200, 200]:
            fixture.policy(prepared['loaded']['design@1'], False)
            probe['revoked'] = True
            markers = [body[k] for k in ('summary', 'limitations', 'recommendations') if body.get(k)]
            markers += [i['notes'] for i in body['items'] if i.get('notes')]
            markers += list(texts.values())
            for url in urls:
                response = actor.request('GET', url)
                # Decode JSON escapes before checking markers, including Chinese content.
                values = list(strings(json.loads(response.body))) if response.status != 200 else [response.body.decode()]
                probe['after'].append({'path': url, 'http': response.status,
                    'bytes': len(response.body), 'sha256': hashlib.sha256(response.body).hexdigest(),
                    'content_matches': sum(any(marker in value for value in values) for marker in markers)})
        probe['reports_after'] = fixture.count(count_sql)
        probe['archive_plans'] = fixture.count("SELECT COUNT(*) FROM archive_plans WHERE task_id='" + task_id + "'")
        probe['state'] = 'boundary_verified' if (
            probe['revoked'] and len(probe['after']) == 3
            and all(r['http'] == 409 and r['content_matches'] == 0 for r in probe['after'])
            and probe['design_in_dependencies'] and probe['reports_before'] == probe['reports_after'] == 1
            and probe['archive_plans'] == 0) else 'boundary_failed'
    write_snapshot(directory / 'h10-revocation.json', probe)
    return snapshot, probe
