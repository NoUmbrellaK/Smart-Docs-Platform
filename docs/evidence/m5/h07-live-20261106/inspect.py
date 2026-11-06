"""Read-only evidence extraction; run from the worktree, never invokes a model."""
import collections
import hashlib
import json
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root / 'test/e2e'))
from benchmark_review import prepare, summarize

private = Path('/tmp/smartdocs-m5-h07-live.40LnB6')
case_dir = private / 'H07'
snapshot = json.loads((case_dir / 'initial/snapshot.json').read_text())
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
assert snapshot['capture']['complete'] and not snapshot.get('archive_plan')
assert snapshot['execution']['automatic_confirmation'] is False
assert snapshot['execution']['automatic_retry'] is False
case = next(c for c in json.loads((root / 'test/fixtures/agent-p0/tasks.json').read_text())['tasks']
            if c['id'] == 'H07')
mapping = snapshot['fixture_mapping']
assert set(mapping) == set(case['present']) and len(mapping) == 8
assert not mapping['internal@1']['approved']
assert mapping['beta_private@1']['project_id'] != snapshot['task']['project_id']
report = snapshot.get('handover_report')
checks = None
if report:
    pair = lambda x: (x['file_id'], x['version_id'])
    allowed = {pair(mapping[ref]) for ref in case['scope']}
    assert len(allowed) == 6
    assert {pair(x) for x in report['selected_versions']} == allowed
    assert {pair(x) for x in report['source_versions']} <= allowed
    assert report['state'] == 'draft'
    forbidden = ['内部口令清单.txt', '乙项目说明.md', 'INTERNAL-ONLY-MARKER-73', 'BETA-ONLY-MARKER-52']
    rendered = json.dumps([report, snapshot['citation_sources']], ensure_ascii=False)
    assert all(word not in rendered for word in forbidden)
    for source in snapshot['citation_sources']:
        c = source['source']['chunk']
        assert pair(c) in allowed and source['reference'] == {k:c[k] for k in ('file_id','version_id','chunk_id')}
        m = next(v for v in mapping.values() if pair(v) == pair(c))
        assert hashlib.sha256(c['text'].encode()).hexdigest() == c['source_sha256'] == m['sha256']
        assert c['line_start'] == 1 and c['line_end'] == c['text'].count('\n') + 1
        assert source['source']['is_current'] is True
    statuses = {x['key']:x['status'] for x in report['report']['items']}
    checks = {'state': report['state'], 'checklist_revision': report['checklist']['revision'],
              'selected_versions': len(report['selected_versions']), 'source_versions': len(report['source_versions']),
              'item_statuses': statuses, 'expected_item_statuses_match': statuses == case['expected']['statuses'],
              'unique_citation_sources': len(snapshot['citation_sources']), 'restricted_material_absent': True}
batches = []
for event in trace:
    if event['event'] == 'model_result': batches.append(0)
    if event['event'] == 'tool_attempt': batches[-1] += 1
review = prepare(snapshot)
assert json.loads((case_dir / 'review.json').read_text()) == review
public = {'case_id': 'H07', 'metadata': snapshot['metadata'], 'admission_http': snapshot['admission_http'],
          'capture_complete': snapshot['capture']['complete'],
          'run_result': {k:snapshot['run_result'].get(k) for k in ('state','error_code','model_calls','kind')},
          'provider_attempts': len(attempts), 'provider_results': len(results),
          'usage': {k:sum(x['usage'][k] for x in results) for k in results[0]['usage']},
          'dispatched_tools_per_model_response': batches,
          'backend_tool_calls': snapshot['tool_calls'],
          'tools_by_name': dict(collections.Counter(x['tool'] for x in snapshot['tool_calls']['items'])),
          'wire_checks': wire, 'report_checks': checks, 'review_summary': summarize(snapshot, review),
          'fixture_sha256': snapshot['fixture_sha256'],
          'private_artifact_sha256': {str(p.relative_to(private)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (case_dir/'initial/snapshot.json',case_dir/'initial/audit.jsonl',case_dir/'summary.json',case_dir/'review.json',private/'wire.jsonl')},
          'business_verdict': None, 'citation_accuracy': None, 'cost_estimate': None}
print(json.dumps(public, ensure_ascii=False, indent=2))
