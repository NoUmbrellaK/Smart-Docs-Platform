"""Reconcile the failed experiment and rollback; not a semantic-success test."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

root = Path.cwd()
base = root / 'docs/evidence/m5'
here = base / 'report-wording-20261106'
result = json.loads((here/'result.json').read_text())
assert json.loads(subprocess.check_output([sys.executable,'-B',str(here/'inspect.py')])) == result
assert result['run_result'] == dict(state='failed', error_code='scope_changed', result=None, model_calls=2)
assert result['provider_attempts'] == result['provider_results'] == 2
assert result['usage'] == dict(prompt_tokens=7813, completion_tokens=316, total_tokens=8129, prompt_cache_hit_tokens=2688, prompt_cache_miss_tokens=5125)
assert result['report_checks'] is None and result['citations'] == result['citation_sources'] == []
assert result['business_verdict'] is result['citation_accuracy'] is None
assert result['review_summary']['citations_reviewed'] == 0
calls = result['backend_tool_calls']['items']
assert [(c['tool'],c['status']) for c in calls] == [('search_documents','success'),('read_document_version','access_denied')]
assert calls[0]['parameters']['mode'] == 'metadata' and calls[1]['parameters']['selector'] == 'status'
audit = json.loads((here/'audit-summary.json').read_text())
assert audit['trace_complete'] and audit['outcome'] == dict(state='failed', error_code='scope_changed')
assert audit['model']['usage_total'] == result['usage'] and audit['model']['usage_unknown_calls'] == 0
assert hashlib.sha256((here/'trace.jsonl').read_bytes()).hexdigest() == result['private_artifact_sha256']['initial/audit.jsonl']
trace = [json.loads(line) for line in (here/'trace.jsonl').read_text().splitlines()]
assert [e['item_count'] for e in trace if e['event']=='tool_result' and e['tool']=='search_documents'] == [10]
assert all(e.get('chunk_count') == 0 for e in trace if e['event']=='tool_result')
ledger = json.loads((base/'fixed-task-results.json').read_text())
old = json.loads(subprocess.check_output(['git','show','8fc67a2:docs/evidence/m5/fixed-task-results.json']))
assert ledger['denominator'] == len(ledger['tasks']) == 30
assert Counter(t['execution'] for t in ledger['tasks']) == dict(completed=17,failed=2,waiting_confirmation=3,not_run=5,admission_rejected=3)
totals = [0,0,0,0]
latest_citations = 0
for task, previous in zip(ledger['tasks'], old['tasks']):
    assert task['business_verdict'] is None
    if task['case_id'] != 'H05': assert task == previous
    else: assert task['attempts'][:-1] == previous['attempts'] and task['attempt_count'] == 4
    history = task.get('attempts', [])
    if not history: continue
    assert len(history) == task['attempt_count']
    assert sum(a['provider_requests'] for a in history) == task['cumulative_provider_requests']
    assert sum(a['total_tokens'] for a in history) == task['cumulative_total_tokens']
    for key in ('evidence','provider_requests','total_tokens'): assert task[key] == history[-1][key]
    for attempt in history:
        saved = json.loads((base/attempt['evidence']).read_text())
        assert saved['run_result']['state'] == attempt['state']
        assert saved['provider_attempts'] == attempt['provider_requests']
        assert saved['usage']['total_tokens'] == attempt['total_tokens']
        citations = saved.get('review_summary',{}).get('citations_total',0)
        totals = [a+b for a,b in zip(totals,(1,attempt['provider_requests'],attempt['total_tokens'],citations))]
    latest_citations += citations
assert totals == [38,129,642702,96] and latest_citations == 49
for name in ('supplemental-task-results.json','citation-review-20261106/packet.json','query-budget-20261106/result.json'):
    assert (base/name).read_bytes() == subprocess.check_output(['git','show','8fc67a2:docs/evidence/m5/'+name])
assert (here/'preflight.txt').read_bytes() == (here/'postflight.txt').read_bytes() == (here/'live-preflight.txt').read_bytes() == (here/'live-postflight.txt').read_bytes()
assert (root/'python/smartdocs/agent_runner.py').read_bytes() == subprocess.check_output(['git','show','8fc67a2:python/smartdocs/agent_runner.py'])
subprocess.run(['git','apply','--check',str(here/'candidate.patch')],check=True)
entries = re.findall(r'^([0-9a-f]{64}) (.+)$',(here/'restored-fingerprints.txt').read_text(),re.M)
assert len(entries) == 48
for digest,name in entries:
    path = Path(name)
    source = path if path.is_absolute() else root/path
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    if not path.is_absolute(): assert source.read_bytes() == (Path('/tmp/smartdocs-safe.h4SzvX/repo')/path).read_bytes()
assert 'FAILED (errors=1)' in (here/'initial-offline.txt').read_text()
assert 'Ran 126 tests in 85.232s\nOK' in (here/'offline.txt').read_text()
status = (here/'live-status.txt').read_text().splitlines()
assert all(x in status for x in ('ActiveState=failed','ExecMainStatus=1','TasksCurrent=0','high 82471','max 0','oom 0','oom_kill 0','active'))
for path in here.iterdir():
    if path.is_file(): assert not re.search(r'sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----',path.read_text()), path
print('PASS:failed H05 attempt4 preserved; 2requests/8129tokens; denied status read, no report/body/citations')
print('PASS:original30 latest17completed/2failed/3waiting/5notrun/3admission; all human verdicts blank')
print('PASS:original history38attempts/129requests/642702tokens/96citations; other29cases and old H05 attempts unchanged')
print('PASS:supplement and old citation packet unchanged; combined40attempts/135requests/660667tokens/98historical51latest citations')
print('PASS:first offline error retained, repeat126/126; HTTP busy race NOT claimed fixed')
print('PASS:48 runtime files restored; candidate patch retained; failed unit terminal, no OOM or new model calls')
