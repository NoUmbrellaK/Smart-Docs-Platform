"""Verify this observation and the full ledger, never infer a human business pass."""
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
e = root / 'docs/evidence/m5/a07-live-20261106'
p = Path('/tmp/smartdocs-m5-a07-live.qpzTqu')
output = io.StringIO()
with contextlib.redirect_stdout(output):
    runpy.run_path(str(e / 'inspect.py'))
r = json.loads((e / 'result.json').read_text())
assert json.loads(output.getvalue()) == r
assert r['run_result'] == dict(state='failed', error_code='scope_changed', model_calls=2, kind=None)
assert r['usage']['total_tokens'] == 5871
assert r['provider_attempts'] == r['provider_results'] == 2
assert r['output_token_limits'] == [1280,1280]
assert r['dispatched_tools_per_model_response'] == [1,1]
assert [(x['tool'],x['status']) for x in r['backend_tool_calls']['items']] == [
    ('read_document_version','success'),('create_archive_plan','conflict')]
checks = r['archive_checks']
assert checks['counts'] == {'archive_plans':0,'archive_decisions':0}
assert checks['state_before_cleanup'] == 'failed'
assert checks['files_unchanged'] and checks['download_hashes_unchanged']
assert checks['plan_http_observations'] == [404,404] and not checks['plan_present']
assert checks['targets'] is None
assert r['review_summary']['citations_total'] == 0
assert r['business_verdict'] is r['review_summary']['business_verdict'] is None
assert (e/'trace.jsonl').read_bytes() == (p/'A07/initial/audit.jsonl').read_bytes()
audit = json.loads((e/'audit-summary.json').read_text())
assert audit['trace_complete'] and audit['model']['usage_total'] == r['usage']
assert audit['outcome'] == {'state':'failed','error_code':'scope_changed'}
for name, digest in r['private_artifact_sha256'].items():
    f = p/name
    assert hashlib.sha256(f.read_bytes()).hexdigest() == digest
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and f.stat().st_uid == 65534
for f in (p,p/'A07',p/'A07/initial'):
    assert stat.S_IMODE(f.stat().st_mode) == 0o700 and f.stat().st_uid == 65534
before = re.findall(r'^([0-9a-f]{64}) (.+)$',(e/'preflight.txt').read_text(),re.M)
after = re.findall(r'^([0-9a-f]{64}) (.+)$',(e/'postflight.txt').read_text(),re.M)
assert before == after and len(after) == 48
for digest,name in after:
    assert hashlib.sha256(Path(name.strip()).read_bytes()).hexdigest() == digest
ledger = json.loads((e.parent/'fixed-task-results.json').read_text())
assert len(ledger['tasks']) == ledger['denominator'] == 30
assert collections.Counter(x['execution'] for x in ledger['tasks']) == dict(
    completed=18,failed=1,not_run=8,admission_rejected=3)
assert (ledger['model_tasks_completed'],ledger['model_tasks_failed'],ledger['model_tasks_not_run'],
        ledger['model_tasks_attempted']) == (18,1,8,19)
totals = [0,0,0,0]
for item in ledger['tasks']:
    assert item['business_verdict'] is None
    attempts = item.get('attempts', [])
    if not attempts: continue
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
assert totals == [33,119,607214,96] and ledger['citations_observed'] == 96
for f in (root/'README.md',e/'README.md',e.parent/'acceptance-index.md'):
    for target in re.findall(r'\]\(([^)]+)\)',f.read_text()):
        if '://' not in target and not target.startswith('#'):
            assert (f.parent/target.split('#')[0]).exists(), target
status = (e/'journal-and-status.txt').read_text()
assert all(x in status for x in ('Result=exit-code','ExecMainStatus=1','ActiveState=failed'))
assert all(x in (e/'postflight.txt').read_text() for x in ('TasksCurrent=0','oom_kill 0','\nactive\n'))
print('PASS A07: 2 requests/5871 tokens; actual conflict, failed/scope_changed, zero plans/decisions, both files unchanged; human verdict blank')
print('PASS private/public hashes, 48 fingerprints, terminal service, ledger 33/119/607214/96')
