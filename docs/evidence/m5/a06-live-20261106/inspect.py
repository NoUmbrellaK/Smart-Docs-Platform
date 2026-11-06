"""Read-only A06 extraction; the model result precedes the test rejection."""
import hashlib
import json
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root/'test/e2e'))
from benchmark_review import prepare, summarize

p = Path('/tmp/smartdocs-m5-a06-live.meDhAR')
c = p/'A06'
s = json.loads((c/'initial/snapshot.json').read_text())
probe = json.loads((p/'probe.json').read_text())
trace = [json.loads(x) for x in (c/'initial/audit.jsonl').read_text().splitlines()]
attempts = [x for x in trace if x['event'] == 'model_attempt']
results = [x for x in trace if x['event'] == 'model_result']
assert 0 < len(attempts) == len(results) <= 6
for index,(a,r) in enumerate(zip(attempts,results),1):
    assert a['sequence'] == r['sequence'] == index and a['request_id'] == r['request_id']
    assert a['max_tokens'] <= 4096 and r['reason'] == 'success' and r['usage']
assert s['capture']['complete']
assert s['execution']['automatic_confirmation'] is s['execution']['automatic_retry'] is False
assert probe['task_id'] == s['task']['id'] == probe['state']['id']
assert probe['state']['state'] == s['run_result']['state']
assert set(s['fixture_mapping']) == {'conflict_a@1','conflict_b@1'}
pair = lambda x: (x['file_id'],x['version_id'])
sources = {pair(x): x for x in s['fixture_mapping'].values()}
assert {pair(x) for x in s['task']['versions']} == set(sources)
assert len(probe['downloads']) == 2 and {pair(x) for x in probe['downloads']} == set(sources)
for x in probe['downloads']:
    assert x['expected_sha256'] == sources[pair(x)]['sha256']
review = prepare(s)
assert json.loads((c/'review.json').read_text()) == review
plan = s['archive_plan']
assert plan == probe['plan_before']
assert plan['task_id'] == s['task']['id'] and plan['project_id'] == s['task']['project_id']
assert len(plan['items']) == 2 and {pair(x) for x in plan['items']} == set(sources)
assert all(x['source_directory_id'] == sources[pair(x)]['directory_id'] for x in plan['items'])
after = probe['plan_after']
assert all(after[k] == plan[k] for k in ('id','revision','content_sha256','items'))
fields = ('id','revision','state','executable','decision','operation_id','has_unresolved','items','content_sha256')
public = {'case_id':'A06','metadata':s['metadata'],'admission_http':s['admission_http'],
    'capture_complete':s['capture']['complete'],
    'run_result':{k:s['run_result'].get(k) for k in ('state','error_code','model_calls','kind')},
    'provider_attempts':len(attempts),'provider_results':len(results),
    'output_token_limits':[x['max_tokens'] for x in attempts],
    'usage':{k:sum(x['usage'][k] for x in results) for k in results[0]['usage']},
    'backend_tool_calls':s['tool_calls'],
    'archive_checks':{'counts':probe['counts'],'state_before_cleanup':probe['state']['state'],
        'files_unchanged':probe['files_before'] == probe['files_before_reject'] == probe['files_after'],
        'downloads':probe['downloads'],
        'download_hashes_unchanged':all(x['http'] == 200 and x['actual_sha256'] == x['expected_sha256']
                                      for x in probe['downloads']),
        'plan_before_reject':{k:plan[k] for k in fields},'plan_after_reject':{k:after[k] for k in fields},
        'decision_http':probe['decision_http'],'confirm_count':probe['confirm_count'],
        'execute_count':probe['execute_count'],'reject_audit_count':probe['reject_audit_count'],
        'operator':'scripted_test_operator_not_model_or_human_ui'},
    'review_summary':summarize(s,review),'fixture_sha256':s['fixture_sha256'],
    'private_artifact_sha256':{str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest()
        for f in (c/'initial/snapshot.json',c/'initial/audit.jsonl',c/'summary.json',c/'review.json',p/'probe.json')},
    'business_verdict':None,'semantic_review_complete':False,'citation_accuracy':None,'cost_estimate':None}
print(json.dumps(public,ensure_ascii=False,indent=2))
