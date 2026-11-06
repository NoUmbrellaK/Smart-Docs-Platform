"""Cross-check A06 artifacts and the fixed ledger; never grade or call a model."""
import collections
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import stat

root = Path.cwd()
e = root/'docs/evidence/m5/a06-live-20261106'
p = Path('/tmp/smartdocs-m5-a06-live.meDhAR')
out = io.StringIO()
with contextlib.redirect_stdout(out):
    runpy.run_path(str(e/'inspect.py'))
r = json.loads((e/'result.json').read_text())
assert json.loads(out.getvalue()) == r
assert r['provider_attempts'] == r['provider_results'] == r['run_result']['model_calls'] == 2
assert r['usage']['total_tokens'] == 6874 and r['output_token_limits'] == [1536,1536]
assert r['run_result']['state'] == 'waiting_confirmation' and r['run_result']['error_code'] is None
assert r['backend_tool_calls']['items'][-1]['tool'] == 'create_archive_plan'
assert all(x['tool'] in ('search_documents','read_document_version','create_archive_plan')
           for x in r['backend_tool_calls']['items'])
assert [(x['tool'],x['status']) for x in r['backend_tool_calls']['items']] == [
    ('read_document_version','success'),('read_document_version','success'),
    ('create_archive_plan','waiting_confirmation')]
assert all(x['parameters']['selector'] == 'range' for x in r['backend_tool_calls']['items'][:2])
c = r['archive_checks']
assert c['counts'] == {'archive_plans':1,'archive_decisions':1}
assert c['confirm_count'] == c['execute_count'] == 0 and c['reject_audit_count'] == 1
assert c['decision_http'] == 200
assert c['state_before_cleanup'] == 'waiting_confirmation'
assert c['files_unchanged'] and c['download_hashes_unchanged']
plan = c['plan_before_reject']
assert plan['state'] == 'preview'
assert plan['executable'] is False and plan['decision'] is plan['operation_id'] is None
assert len(plan['items']) == 2 and plan['revision'] == 1
assert all(x['reason'].strip() for x in plan['items'])
rejected = c['plan_after_reject']
assert rejected['state'] == 'rejected' and rejected['decision'] == 'reject'
assert rejected['operation_id'] is None and rejected['executable'] is False
assert r['review_summary']['citations_total'] == 0
assert r['business_verdict'] is r['review_summary']['business_verdict'] is None
assert r['semantic_review_complete'] is False
assert (e/'trace.jsonl').read_bytes() == (p/'A06/initial/audit.jsonl').read_bytes()
audit = json.loads((e/'audit-summary.json').read_text())
assert audit['trace_complete'] and audit['model']['usage_total'] == r['usage']
assert audit['outcome']['state'] == 'waiting_confirmation'
for name,digest in r['private_artifact_sha256'].items():
    f = p/name
    assert hashlib.sha256(f.read_bytes()).hexdigest() == digest
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and f.stat().st_uid == 65534
for f in (p,p/'A06',p/'A06/initial'):
    assert stat.S_IMODE(f.stat().st_mode) == 0o700 and f.stat().st_uid == 65534
before = re.findall(r'^([0-9a-f]{64}) (.+)$',(e/'preflight.txt').read_text(),re.M)
after = re.findall(r'^([0-9a-f]{64}) (.+)$',(e/'postflight.txt').read_text(),re.M)
assert before == after and len(after) == 49
for digest,name in after:
    assert hashlib.sha256(Path(name.strip()).read_bytes()).hexdigest() == digest
ledger = json.loads((e.parent/'fixed-task-results.json').read_text())
assert len(ledger['tasks']) == ledger['denominator'] == 30
assert collections.Counter(x['execution'] for x in ledger['tasks']) == dict(
    completed=18,failed=1,waiting_confirmation=3,not_run=5,admission_rejected=3)
assert tuple(ledger['model_tasks_'+k] for k in
             ('completed','failed','waiting_confirmation','not_run','attempted')) == (18,1,3,5,22)
totals = [0,0,0,0]
for item in ledger['tasks']:
    assert item['business_verdict'] is None
    attempts = item.get('attempts',[])
    if not attempts:
        continue
    assert len(attempts) == item['attempt_count']
    assert sum(a['provider_requests'] for a in attempts) == item['cumulative_provider_requests']
    assert sum(a['total_tokens'] for a in attempts) == item['cumulative_total_tokens']
    for attempt in attempts:
        saved = json.loads((e.parent/attempt['evidence']).read_text())
        assert saved['run_result']['state'] == attempt['state']
        assert saved['provider_attempts'] == attempt['provider_requests']
        assert saved['usage']['total_tokens'] == attempt['total_tokens']
        totals = [x+y for x,y in zip(totals,[1,attempt['provider_requests'],attempt['total_tokens'],
                                           saved.get('review_summary',{}).get('citations_total',0)])]
assert totals == [36,123+r['provider_attempts'],618817+r['usage']['total_tokens'],96]
for f in (root/'README.md',e/'README.md',e.parent/'acceptance-index.md'):
    for target in re.findall(r'\]\(([^)]+)\)',f.read_text()):
        if '://' not in target and not target.startswith('#'):
            assert (f.parent/target.split('#')[0]).exists(),target
assert all(x in (e/'journal-and-status.txt').read_text() for x in
           ('Result=exit-code','ExecMainStatus=3','ActiveState=failed'))
assert all(x in (e/'postflight.txt').read_text() for x in ('TasksCurrent=0','oom_kill 0','\nactive\n'))
offline = Path('/tmp/smartdocs-m5-a06-offline.HjqUgY')
red = json.loads((offline/'red.json').read_text())
green = json.loads((offline/'green.json').read_text())
assert red['counts'] == {'archive_plans':1,'archive_decisions':0}
assert green['counts'] == {'archive_plans':1,'archive_decisions':1}
assert green['plan_after']['decision'] == 'reject' and green['confirm_count'] == green['execute_count'] == 0
assert hashlib.sha256((e/'red-observe.py').read_bytes()).hexdigest() in (e/'red-start.txt').read_text()
assert hashlib.sha256((e/'observe.py').read_bytes()).hexdigest() in (e/'offline-start.txt').read_text()
assert "AssertionError: {'archive_plans': 1, 'archive_decisions': 0}" in (e/'red-journal.txt').read_text()
assert 'PASS A06 test-operator rejection' in (e/'offline-journal.txt').read_text()
print('PASS A06: model proposal and scripted revision-bound rejection, no confirmation or move; human verdict blank')
print('PASS private/public hashes, 49 fingerprints, terminal service and complete ledger:',totals)
