"""Read-only A07 extraction; no credentials, provider requests or business grading."""
import collections
import hashlib
import json
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root / 'test/e2e'))
from benchmark_review import prepare, summarize

private = Path('/tmp/smartdocs-m5-a07-live.qpzTqu')
case_dir = private / 'A07'
snapshot = json.loads((case_dir / 'initial/snapshot.json').read_text())
probe = json.loads((private / 'probe.json').read_text())
trace = [json.loads(x) for x in (case_dir / 'initial/audit.jsonl').read_text().splitlines()]
wire = [json.loads(x) for x in (private / 'wire.jsonl').read_text().splitlines()]
attempts = [x for x in trace if x['event'] == 'model_attempt']
results = [x for x in trace if x['event'] == 'model_result']
assert 0 < len(wire) == len(attempts) == len(results) <= 6
assert [x['sequence'] for x in wire] == list(range(1, len(wire) + 1))
assert [x['request_bytes'] for x in wire] == [x['request_bytes'] for x in attempts]
assert all(x['restricted_matches'] == 0 and x['forwarded'] for x in wire)
assert all(x['max_tokens'] <= 4096 for x in attempts)
assert all(x['reason'] == 'success' and x['usage'] for x in results)
assert snapshot['capture']['complete']
assert snapshot['execution']['automatic_confirmation'] is False
assert snapshot['execution']['automatic_retry'] is False
assert snapshot['task']['id'] == probe['task_id'] == probe['state']['id']
assert snapshot['run_result']['state'] == probe['state']['state']
mapping = snapshot['fixture_mapping']
assert set(mapping) == {'deployment@2', 'collision@1'}
pair = lambda x: (x['file_id'], x['version_id'])
assert {pair(x) for x in snapshot['task']['versions']} == {pair(mapping['deployment@2'])}
assert len(probe['downloads']) == 2
for row in probe['downloads']:
    assert pair(row) == pair(mapping[row['source']])
    assert row['expected_sha256'] == mapping[row['source']]['sha256']
review = prepare(snapshot)
assert json.loads((case_dir / 'review.json').read_text()) == review
batches = []
for event in trace:
    if event['event'] == 'model_result': batches.append(0)
    if event['event'] == 'tool_attempt': batches[-1] += 1
plan = snapshot.get('archive_plan')
public = {'case_id': 'A07', 'metadata': snapshot['metadata'], 'admission_http': snapshot['admission_http'],
    'capture_complete': snapshot['capture']['complete'],
    'run_result': {k:snapshot['run_result'].get(k) for k in ('state','error_code','model_calls','kind')},
    'provider_attempts': len(attempts), 'provider_results': len(results),
    'output_token_limits': [x['max_tokens'] for x in attempts],
    'usage': {k:sum(x['usage'][k] for x in results) for k in results[0]['usage']},
    'dispatched_tools_per_model_response': batches, 'backend_tool_calls': snapshot['tool_calls'],
    'tools_by_name': dict(collections.Counter(x['tool'] for x in snapshot['tool_calls']['items'])),
    'wire_checks': wire, 'archive_checks': {
        'state_before_cleanup': probe['state']['state'], 'counts': probe['counts'],
        'files_unchanged': probe['files_before'] == probe['files_after'],
        'downloads': probe['downloads'],
        'download_hashes_unchanged': all(x['http'] == 200 and x['actual_sha256'] == x['expected_sha256']
                                       for x in probe['downloads']),
        'plan_present': plan is not None,
        'plan_http_observations': [r['http'] for r in snapshot['capture']['reads']
                                   if r['path'].endswith('/archive-plan')],
        'plan_state': plan.get('state') if plan else None,
        'targets': [x['target_directory_id'] for x in plan['items']] if plan else None},
    'review_summary': summarize(snapshot, review), 'fixture_sha256': snapshot['fixture_sha256'],
    'private_artifact_sha256': {str(p.relative_to(private)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (case_dir/'initial/snapshot.json',case_dir/'initial/audit.jsonl',case_dir/'summary.json',
                  case_dir/'review.json',private/'wire.jsonl',private/'probe.json')},
    'business_verdict': None, 'citation_accuracy': None, 'cost_estimate': None}
print(json.dumps(public, ensure_ascii=False, indent=2))
