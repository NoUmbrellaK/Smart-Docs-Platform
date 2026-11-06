"""Read-only H10 extraction; no provider calls and no automatic business verdict."""
import collections
import hashlib
import json
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root / 'test/e2e'))
from benchmark_review import prepare, summarize

private = Path('/tmp/smartdocs-m5-h10-live.HGop4D')
case_dir = private / 'H10'
snapshot = json.loads((case_dir / 'initial/snapshot.json').read_text())
trace = [json.loads(x) for x in (case_dir / 'initial/audit.jsonl').read_text().splitlines()]
wire = [json.loads(x) for x in (case_dir / 'h10-wire.jsonl').read_text().splitlines()]
probe = json.loads((case_dir / 'h10-revocation.json').read_text())
attempts = [x for x in trace if x['event'] == 'model_attempt']
results = [x for x in trace if x['event'] == 'model_result']
assert 0 < len(wire) == len(attempts) == len(results) <= 6
assert [x['sequence'] for x in wire] == list(range(1, len(wire) + 1))
assert [x['sequence'] for x in results] == [x['sequence'] for x in wire]
assert [x['request_bytes'] for x in wire] == [x['request_bytes'] for x in attempts]
assert [x['request_id'] for x in results] == [x['request_id'] for x in attempts]
assert all(x['max_tokens'] <= 4096 for x in attempts)
assert snapshot['capture']['complete'] and not snapshot.get('archive_plan')
assert snapshot['execution']['automatic_confirmation'] is False
assert snapshot['execution']['automatic_retry'] is False
case = next(c for c in json.loads((root / 'test/fixtures/agent-p0/tasks.json').read_text())['tasks'] if c['id'] == 'H10')
mapping = snapshot['fixture_mapping']
assert set(mapping) == set(case['present']) and len(mapping) == 2
report = snapshot.get('handover_report')
checks = None
if report:
    pair = lambda x: (x['file_id'], x['version_id'])
    selected = {pair(mapping[ref]) for ref in case['scope']}
    assert {pair(x) for x in report['selected_versions']} == selected
    assert {pair(x) for x in report['source_versions']} <= selected
    assert report['state'] == 'draft'
    for source in snapshot['citation_sources']:
        c = source['source']['chunk']
        assert pair(c) in selected and source['reference'] == {k:c[k] for k in ('file_id','version_id','chunk_id')}
        m = next(v for v in mapping.values() if pair(v) == pair(c))
        assert hashlib.sha256(c['text'].encode()).hexdigest() == c['source_sha256'] == m['sha256']
        assert c['line_start'] == 1 and c['line_end'] == c['text'].count('\n') + 1
        assert source['source']['is_current'] is True
    statuses = {x['key']:x['status'] for x in report['report']['items']}
    checks = {'state': report['state'], 'checklist_revision': report['checklist']['revision'],
              'selected_versions': len(report['selected_versions']), 'source_versions': len(report['source_versions']),
              'item_statuses': statuses, 'expected_item_statuses_match': statuses == case['expected']['statuses'],
              'unique_citation_sources': len(snapshot['citation_sources'])}
batches = []
for event in trace:
    if event['event'] == 'model_result': batches.append(0)
    if event['event'] == 'tool_attempt': batches[-1] += 1
review = prepare(snapshot)
assert json.loads((case_dir / 'review.json').read_text()) == review
usage = [x['usage'] for x in results if x.get('usage')]
public = {'case_id': 'H10', 'metadata': snapshot['metadata'], 'admission_http': snapshot['admission_http'],
          'capture_complete': snapshot['capture']['complete'],
          'run_result': {k:snapshot['run_result'].get(k) for k in ('state','error_code','model_calls','kind')},
          'provider_attempts': len(attempts), 'provider_results': len(results),
          'usage': {k:sum(x[k] for x in usage) for k in set.intersection(*(set(x) for x in usage))} if usage else None,
          'dispatched_tools_per_model_response': batches, 'backend_tool_calls': snapshot['tool_calls'],
          'tools_by_name': dict(collections.Counter(x['tool'] for x in snapshot['tool_calls']['items'])),
          'wire_checks': wire, 'report_checks': checks, 'post_revocation': probe,
          'review_summary': summarize(snapshot, review), 'fixture_sha256': snapshot['fixture_sha256'],
          'private_artifact_sha256': {str(p.relative_to(private)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (case_dir/'initial/snapshot.json',case_dir/'initial/audit.jsonl',case_dir/'summary.json',
                        case_dir/'review.json',case_dir/'h10-wire.jsonl',case_dir/'h10-revocation.json')},
          'business_verdict': None, 'citation_accuracy': None, 'cost_estimate': None}
print(json.dumps(public, ensure_ascii=False, indent=2))
