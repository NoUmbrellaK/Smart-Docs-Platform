"""Extract actual A08 observations without model calls or automatic grading."""
import hashlib
import json
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root/'test/e2e'))
from benchmark_review import prepare, summarize

private = Path('/tmp/smartdocs-m5-a08-live.iPMQJV')
case_dir = private/'A08'
snapshot = json.loads((case_dir/'initial/snapshot.json').read_text())
probe = json.loads((private/'probe.json').read_text())
trace = [json.loads(line) for line in (case_dir/'initial/audit.jsonl').read_text().splitlines()]
wire = [json.loads(line) for line in (private/'wire.jsonl').read_text().splitlines()]
attempts = [x for x in trace if x['event'] == 'model_attempt']
results = [x for x in trace if x['event'] == 'model_result']
assert 0 < len(wire) == len(attempts) == len(results) <= 6
for index, (w,a,r) in enumerate(zip(wire,attempts,results),1):
    assert w['sequence'] == a['sequence'] == r['sequence'] == index
    assert w['request_bytes'] == a['request_bytes'] and a['request_id'] == r['request_id']
    assert a['max_tokens'] <= 4096 and r['reason'] == 'success' and r['usage']
assert snapshot['capture']['complete']
assert snapshot['execution']['automatic_confirmation'] is snapshot['execution']['automatic_retry'] is False
assert probe['task_id'] == snapshot['task']['id'] == probe['state']['id']
assert probe['state']['state'] == snapshot['run_result']['state']
assert set(snapshot['fixture_mapping']) == {'injection@1'}
source = snapshot['fixture_mapping']['injection@1']
pair = lambda x: (x['file_id'],x['version_id'])
assert {pair(x) for x in snapshot['task']['versions']} == {pair(source)}
assert probe['download']['expected_sha256'] == source['sha256']
review = prepare(snapshot)
assert json.loads((case_dir/'review.json').read_text()) == review
plan = snapshot.get('archive_plan')
if plan:
    assert plan['task_id'] == snapshot['task']['id'] and plan['project_id'] == snapshot['task']['project_id']
    assert len(plan['items']) == 1 and pair(plan['items'][0]) == pair(source)
    assert plan['items'][0]['source_directory_id'] == source['directory_id']
public = {'case_id':'A08','metadata':snapshot['metadata'],'admission_http':snapshot['admission_http'],
    'capture_complete':snapshot['capture']['complete'],
    'run_result':{k:snapshot['run_result'].get(k) for k in ('state','error_code','model_calls','kind')},
    'provider_attempts':len(attempts),'provider_results':len(results),
    'output_token_limits':[x['max_tokens'] for x in attempts],
    'usage':{k:sum(x['usage'][k] for x in results) for k in results[0]['usage']},
    'backend_tool_calls':snapshot['tool_calls'],'wire_checks':wire,
    'injection_body_in_successful_request':any(w['injection_body_present'] for w in wire),
    'archive_checks':{'counts':probe['counts'],'state_before_cleanup':probe['state']['state'],
        'files_unchanged':probe['files_before'] == probe['files_after'],'download':probe['download'],
        'download_hash_unchanged':probe['download']['http'] == 200 and
            probe['download']['actual_sha256'] == probe['download']['expected_sha256'],
        'plan':{k:plan.get(k) for k in ('id','revision','state','executable','decision','operation_id',
                                      'has_unresolved','items')} if plan else None},
    'review_summary':summarize(snapshot,review),'fixture_sha256':snapshot['fixture_sha256'],
    'private_artifact_sha256':{str(p.relative_to(private)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (case_dir/'initial/snapshot.json',case_dir/'initial/audit.jsonl',case_dir/'summary.json',
                  case_dir/'review.json',private/'wire.jsonl',private/'probe.json')},
    'business_verdict':None,'citation_accuracy':None,'cost_estimate':None}
print(json.dumps(public,ensure_ascii=False,indent=2))
