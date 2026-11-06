"""Read-only H08 evidence extraction; no network or model calls."""
import collections
import hashlib
import json
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root / 'test/e2e'))
from benchmark_review import prepare, summarize

p = Path('/tmp/smartdocs-m5-h08-live.OUrwx4/H08')
s = json.loads((p/'initial/snapshot.json').read_text())
trace = [json.loads(x) for x in (p/'initial/audit.jsonl').read_text().splitlines()]
attempts = [x for x in trace if x['event'] == 'model_attempt']
results = [x for x in trace if x['event'] == 'model_result']
assert 0 < len(attempts) == len(results) <= 6
assert all(x['max_tokens'] <= 4096 for x in attempts)
assert all(x['reason'] == 'success' and x['usage'] for x in results)
assert s['capture']['complete'] and not s.get('archive_plan')
assert s['execution']['automatic_confirmation'] is s['execution']['automatic_retry'] is False
assert set(s['fixture_mapping']) == {'deployment@2'}
m = s['fixture_mapping']['deployment@2']
assert m['approved'] and m['parse_state'] == 'completed'
report = s.get('handover_report')
checks = None
if report:
    selected = [{k:m[k] for k in ('file_id','version_id')}]
    assert report['selected_versions'] == report['source_versions'] == selected
    assert report['state'] == 'draft' and report['checklist']['revision'] == 1
    flags = {x['key']:x['required'] for x in report['checklist']['items']}
    assert flags == {'deploy': True, 'test': False}
    assert {x['key']:x['required'] for x in report['report']['items']} == flags
    for source in s['citation_sources']:
        c = source['source']['chunk']
        assert source['reference'] == {k:c[k] for k in ('file_id','version_id','chunk_id')}
        assert all(c[k] == m[k] for k in ('file_id','version_id'))
        assert hashlib.sha256(c['text'].encode()).hexdigest() == m['sha256'] == c['source_sha256']
        assert c['line_start'] == 1 and c['line_end'] == c['text'].count('\n')+1
        assert source['source']['is_current'] is True
        assert 'ORBIT-27' in c['text'] and 'STARBRIDGE_DB_DSN' in c['text'] and '8087' in c['text']
    statuses = {x['key']:x['status'] for x in report['report']['items']}
    checks = {'state':report['state'], 'checklist_revision':1, 'required_flags':flags,
              'selected_versions':len(report['selected_versions']), 'source_versions':len(report['source_versions']),
              'item_statuses':statuses, 'expected_item_statuses_match':statuses == {'deploy':'found','test':'missing'},
              'unique_citation_sources':len(s['citation_sources'])}
batches = []
for event in trace:
    if event['event'] == 'model_result': batches.append(0)
    if event['event'] == 'tool_attempt': batches[-1] += 1
review = prepare(s)
assert json.loads((p/'review.json').read_text()) == review
value = {'case_id':'H08', 'metadata':s['metadata'], 'admission_http':s['admission_http'],
    'capture_complete':s['capture']['complete'],
    'run_result':{k:s['run_result'].get(k) for k in ('state','error_code','model_calls','kind')},
    'provider_attempts':len(attempts), 'provider_results':len(results),
    'usage':{k:sum(x['usage'][k] for x in results) for k in results[0]['usage']},
    'dispatched_tools_per_model_response':batches, 'backend_tool_calls':s['tool_calls'],
    'tools_by_name':dict(collections.Counter(x['tool'] for x in s['tool_calls']['items'])),
    'report_checks':checks, 'review_summary':summarize(s,review), 'fixture_sha256':s['fixture_sha256'],
    'private_artifact_sha256':{name:hashlib.sha256((p/name).read_bytes()).hexdigest()
        for name in ('initial/snapshot.json','initial/audit.jsonl','summary.json','review.json')},
    'business_verdict':None, 'citation_accuracy':None, 'cost_estimate':None}
print(json.dumps(value,ensure_ascii=False,indent=2))
