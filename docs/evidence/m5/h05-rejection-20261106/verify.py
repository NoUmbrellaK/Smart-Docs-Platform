"""Reconcile private capture, public diagnostics and complete attempt ledger; no model calls."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

base=Path('docs/evidence/m5'); here=base/'h05-rejection-20261106'
def run(script): return subprocess.check_output([sys.executable,'-B',str(here/script)])
result=json.loads((here/'result.json').read_text())
assert result==json.loads(run('inspect.py'))
assert result['attempt_number']==6 and result['capture_complete']
assert result['metadata']['code_revision']=='4f1109a4adb768810748cf55151cf90865156150'
assert result['business_verdict'] is result['citation_accuracy'] is result['cost_estimate'] is None
audit=json.loads((here/'audit-summary.json').read_text())
assert audit['trace_complete'] and audit['model']['usage_unknown_calls']==0
assert audit['model']['usage_total']==result['usage']
assert audit['model']['attempts']==audit['model']['results']==result['provider_attempts']==result['provider_results']
assert audit['outcome']['state']==result['run_result']['state']
assert hashlib.sha256((here/'trace.jsonl').read_bytes()).hexdigest()==result['private_artifact_sha256']['initial/audit.jsonl']
assert json.loads(run('check-scope.py'))==json.loads((here/'payload-scope.json').read_text())
assert json.loads(run('check-citations.py'))==json.loads((here/'citation-check.json').read_text())
assert result['run_result']['state']=='completed' and result['run_result']['kind']=='handover_report'
assert result['provider_attempts']==4 and result['usage']['total_tokens']==29467
assert result['tools_by_name']=={'search_documents':1,'read_document_version':8,'save_handover_report':1}
assert all(c['status']=='success' and 'rejection_reason' not in c['parameters'] for c in result['backend_tool_calls']['items'])
assert all(v=='found' for v in result['report_checks']['item_statuses'].values())
assert result['report_checks']['item_statuses']!=result['report_checks']['expected_item_statuses']
assert run('fingerprints.py')==(here/'preflight.txt').read_bytes()==(here/'postflight.txt').read_bytes()
ledger=json.loads((base/'fixed-task-results.json').read_text())
old=json.loads(subprocess.check_output(['git','show','4f1109a:docs/evidence/m5/fixed-task-results.json']))
assert ledger['denominator']==len(ledger['tasks'])==30
totals=[0,0,0,0]; latest=0
for task,previous in zip(ledger['tasks'],old['tasks']):
    assert task['business_verdict'] is None
    if task['case_id']!='H05': assert task==previous
    else:
        assert task['attempts'][:-1]==previous['attempts'] and task['attempt_count']==6
        assert task['execution']==result['run_result']['state']
    history=task.get('attempts',[])
    if not history: continue
    assert len(history)==task['attempt_count']
    assert sum(a['provider_requests'] for a in history)==task['cumulative_provider_requests']
    assert sum(a['total_tokens'] for a in history)==task['cumulative_total_tokens']
    for key in ('evidence','provider_requests','total_tokens'): assert task[key]==history[-1][key]
    for attempt in history:
        saved=json.loads((base/attempt['evidence']).read_text())
        assert saved['run_result']['state']==attempt['state'] and saved['provider_attempts']==attempt['provider_requests']
        assert saved['usage']['total_tokens']==attempt['total_tokens']
        citations=saved['review_summary']['citations_total'] if 'review_summary' in saved else saved['citations_total']
        totals=[a+b for a,b in zip(totals,(1,attempt['provider_requests'],attempt['total_tokens'],citations))]
    latest+=citations
assert totals[:3]==[40,133+result['provider_attempts'],671340+result['usage']['total_tokens']]
assert totals[3]==96+result['review_summary']['citations_total'] and latest==49+result['review_summary']['citations_total']
assert totals==[40,137,700807,104] and latest==57
states=Counter(t['execution'] for t in ledger['tasks'])
assert ledger['model_tasks_completed']==states['completed'] and ledger['model_tasks_failed']==states['failed']
assert ledger['citations_observed']==totals[3]
for name in ('supplemental-task-results.json','citation-review-20261106/packet.json','h05-diagnostic-20261106/result.json'):
    assert (base/name).read_bytes()==subprocess.check_output(['git','show','4f1109a:docs/evidence/m5/'+name])
status=(here/'live-status.txt').read_text().splitlines()
assert all(s in status for s in ('TasksCurrent=0','high 82471','max 0','oom 0','oom_kill 0','active'))
assert ('ActiveState=failed' in status) or ('ActiveState=inactive' in status)
for path in here.iterdir():
    if path.is_file(): assert not re.search(r'sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----',path.read_text()),path
print('PASS: private capture/audit/scope/173 fingerprints reconcile; all old attempts and other29 cases preserved')
print('Original30 states:',dict(states),'history attempts/requests/tokens/citations:',totals,'latest citations:',latest)
print('PASS: human verdicts blank; test unit terminal/slice empty; no new high/max/OOM; production MySQL active')
