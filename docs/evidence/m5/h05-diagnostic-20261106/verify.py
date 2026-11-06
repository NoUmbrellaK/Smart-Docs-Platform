"""Read-only reconciliation, not a business or semantic success verdict."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

base = Path('docs/evidence/m5')
here = base/'h05-diagnostic-20261106'
result = json.loads((here/'result.json').read_text())
assert result == json.loads(subprocess.check_output([sys.executable,'-B',str(here/'inspect.py')]))
assert result['run_result']==dict(state='failed',error_code='repeated_tool_error',result=None,model_calls=4)
assert result['provider_attempts']==result['provider_results']==4
assert result['usage']==dict(prompt_tokens=22813,completion_tokens=5825,total_tokens=28638,
                             prompt_cache_hit_tokens=15104,prompt_cache_miss_tokens=7709)
assert result['report_checks'] is None and result['citations']==result['citation_sources']==[]
assert result['business_verdict'] is result['citation_accuracy'] is result['cost_estimate'] is None
calls = result['backend_tool_calls']['items']
assert [(c['tool'],c['status']) for c in calls]==[
    ('search_documents','success'), *[('save_handover_report','invalid_request')]*3]
assert calls[0]['parameters']==dict(mode='metadata',query_bytes=0)
assert all(c['parameters']==dict(item_count=6) for c in calls[1:])
audit = json.loads((here/'audit-summary.json').read_text())
assert audit['trace_complete'] and 'read_targets' not in audit
assert audit['outcome']==dict(state='failed',error_code='repeated_tool_error')
assert audit['model']['usage_total']==result['usage'] and audit['model']['usage_unknown_calls']==0
assert hashlib.sha256((here/'trace.jsonl').read_bytes()).hexdigest()==result['private_artifact_sha256']['initial/audit.jsonl']
trace = [json.loads(line) for line in (here/'trace.jsonl').read_text().splitlines()]
assert [e['item_count'] for e in trace if e['event']=='tool_result' and e['tool']=='search_documents']==[10]
assert all(e['chunk_count']==0 for e in trace if e['event']=='tool_result')
assert [e['usage']['completion_tokens'] for e in trace if e['event']=='model_result']==[100,1939,1893,1893]
ledger = json.loads((base/'fixed-task-results.json').read_text())
old = json.loads(subprocess.check_output(['git','show','5baa273:docs/evidence/m5/fixed-task-results.json']))
assert ledger['denominator']==len(ledger['tasks'])==30
assert Counter(t['execution'] for t in ledger['tasks'])==dict(completed=17,failed=2,waiting_confirmation=3,not_run=5,admission_rejected=3)
totals = [0,0,0,0]; latest = 0
for task,previous in zip(ledger['tasks'],old['tasks']):
    assert task['business_verdict'] is None
    if task['case_id']!='H05': assert task==previous
    else: assert task['attempts'][:-1]==previous['attempts'] and task['attempt_count']==5
    history = task.get('attempts',[])
    if not history: continue
    assert len(history)==task['attempt_count']
    assert sum(a['provider_requests'] for a in history)==task['cumulative_provider_requests']
    assert sum(a['total_tokens'] for a in history)==task['cumulative_total_tokens']
    for key in ('evidence','provider_requests','total_tokens'): assert task[key]==history[-1][key]
    for attempt in history:
        saved = json.loads((base/attempt['evidence']).read_text())
        assert saved['run_result']['state']==attempt['state'] and saved['provider_attempts']==attempt['provider_requests']
        assert saved['usage']['total_tokens']==attempt['total_tokens']
        citations = saved['review_summary']['citations_total'] if 'review_summary' in saved else saved['citations_total']
        totals = [a+b for a,b in zip(totals,(1,attempt['provider_requests'],attempt['total_tokens'],citations))]
    latest += citations
assert totals==[39,133,671340,96] and latest==49
for name in ('supplemental-task-results.json','citation-review-20261106/packet.json','report-wording-20261106/result.json'):
    assert (base/name).read_bytes()==subprocess.check_output(['git','show','5baa273:docs/evidence/m5/'+name])
assert (here/'preflight.txt').read_bytes()==(here/'postflight.txt').read_bytes()
assert subprocess.check_output([sys.executable,'-B',str(here/'fingerprints.py')])==(here/'postflight.txt').read_bytes()
status = (here/'live-status.txt').read_text().splitlines()
assert all(s in status for s in ('ActiveState=failed','ExecMainStatus=1','TasksCurrent=0','high 82471','max 0','oom 0','oom_kill 0','active'))
for path in here.iterdir():
    if path.is_file(): assert not re.search(r'sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----',path.read_text()),path
print('PASS: H05 attempt5 failed/repeated_tool_error; 4requests/28638tokens; 1metadata search/3rejected saves; no read/report/citations')
print('PASS: 48 runtime fingerprints match; private hashes and audit reconcile; old H05 attempts and other29cases unchanged')
print('PASS: original30 latest17completed/2failed/3waiting/5notrun/3admission; history39attempts/133requests/671340tokens/96citations; latest49')
print('PASS: supplement unchanged; combined41attempts/139requests/689305tokens/98historical51latest citations; human verdicts blank')
print('PASS: failed unit terminal, slice empty, resource counters unchanged; exact report rejection cause NOT established')
