"""Export only approved synthetic case results; never fill human verdicts."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import stat
import sys

root = Path.cwd()
sys.path.insert(0, str(root / 'test/e2e'))
from benchmark_review import prepare, summarize

private = Path('/tmp/smartdocs-m5-h05-rejection.9mjMWF/H05')
raw = {}
for name in ('initial/snapshot.json', 'initial/audit.jsonl', 'summary.json', 'review.json'):
    path = private / name
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == 65534
    assert info.st_size <= 2 * 1024 * 1024
    raw[name] = path.read_bytes()
snapshot, review = json.loads(raw['initial/snapshot.json']), json.loads(raw['review.json'])
assert review == prepare(snapshot) and snapshot['case_id'] == 'H05' and snapshot['capture']['complete']
assert not snapshot['execution']['automatic_retry'] and not snapshot['execution']['automatic_confirmation']
assert snapshot.get('archive_plan') is None
case = next(c for c in json.loads((root/'test/fixtures/agent-p0/tasks.json').read_text())['tasks'] if c['id']=='H05')
mapping = snapshot['fixture_mapping']
assert set(mapping) == set(case['present']) == set(case['scope']) and len(mapping) == 11
assert all(m['approved'] and m['parse_state']=='completed' for m in mapping.values())
trace = [json.loads(line) for line in raw['initial/audit.jsonl'].splitlines()]
attempts = [e for e in trace if e['event']=='model_attempt']
models = [e for e in trace if e['event']=='model_result']
assert len(attempts) <= 6 and all(e['max_tokens']==4096 for e in attempts)
assert len(models)==len(attempts) and all(isinstance(e['usage'],dict) for e in models)
calls = snapshot['tool_calls']['items']
report = snapshot.get('handover_report')
checks = None
if report:
    selected = sorted([{k: mapping[ref][k] for k in ('file_id','version_id')} for ref in case['scope']],key=lambda v:v['file_id'])
    assert report['selected_versions']==selected
    checks = {'state':report['state'], 'report':report['report'],
        'selected_version_count':len(selected), 'source_version_count':len(report['source_versions']),
        'source_dependencies_match_first_ten_file_ids':report['source_versions']==selected[:10],
        'item_statuses':{i['key']:i['status'] for i in report['report']['items']},
        'expected_item_statuses':case['expected']['statuses'], 'raw_pagination_wire_retained':False}
sources = []
for source in snapshot['citation_sources']:
    labels = [label for label,m in mapping.items() if all(m[k]==source['reference'][k] for k in ('file_id','version_id'))]
    assert len(labels)==1
    sources.append({**source,'fixture':labels[0]})
print(json.dumps({'case_id':'H05','attempt_number':6,'metadata':snapshot['metadata'],
    'input':review['input'],'expected':review['expected'],'admission_http':snapshot['admission_http'],
    'run_result':snapshot['run_result'],'capture_complete':True,'backend_tool_calls':snapshot['tool_calls'],
    'provider_attempts':len(attempts),'provider_results':len(models),
    'max_tokens_observed':[e['max_tokens'] for e in attempts],
    'usage':{k:sum(e['usage'][k] for e in models) for k in
             ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')},
    'tools_by_name':dict(Counter(c['tool'] for c in calls)),'report_checks':checks,
    'citations':review['citations'],'citation_sources':sources,'review_summary':summarize(snapshot,review),
    'business_verdict':None,'citation_accuracy':None,'cost_estimate':None,'fixture_sha256':review['fixture_sha256'],
    'private_artifact_sha256':{name:hashlib.sha256(value).hexdigest() for name,value in raw.items()}},ensure_ascii=False,indent=2))
