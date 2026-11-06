"""Cross-check A10 artifacts and the fixed ledger; never grade or call a model."""
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
e = root/'docs/evidence/m5/a10-live-20261106'
p = Path('/tmp/smartdocs-m5-a10-live.0nIzpW')
out = io.StringIO()
with contextlib.redirect_stdout(out):
    runpy.run_path(str(e/'inspect.py'))
r = json.loads((e/'result.json').read_text())
assert json.loads(out.getvalue()) == r
assert r['provider_attempts'] == r['provider_results'] == 2 and r['usage']['total_tokens'] == 5826
assert r['output_token_limits'] == [1280,1280]
assert r['run_result']['state'] == 'waiting_confirmation' and r['run_result']['error_code'] is None
assert [(x['tool'],x['status']) for x in r['backend_tool_calls']['items']] == [
    ('read_document_version','success'),('create_archive_plan','waiting_confirmation')]
c = r['archive_checks']
assert c['counts'] == {'archive_plans':1,'archive_decisions':0}
assert c['state_before_cleanup'] == 'waiting_confirmation'
assert c['files_unchanged'] and c['download_hash_unchanged']
plan = c['plan']
assert plan['state'] == 'preview' and plan['has_unresolved'] is True
assert plan['executable'] is False and plan['decision'] is plan['operation_id'] is None
assert len(plan['items']) == 1 and plan['items'][0]['target_directory_id'] is None
assert plan['revision'] == 1 and plan['items'][0]['state'] == 'needs_review'
assert plan['items'][0]['reason'].strip()
assert r['review_summary']['citations_total'] == 0
assert r['business_verdict'] is r['review_summary']['business_verdict'] is None
assert (e/'trace.jsonl').read_bytes() == (p/'A10/initial/audit.jsonl').read_bytes()
audit = json.loads((e/'audit-summary.json').read_text())
assert audit['trace_complete'] and audit['model']['usage_total'] == r['usage']
assert audit['outcome']['state'] == 'waiting_confirmation'
for name,digest in r['private_artifact_sha256'].items():
    f = p/name
    assert hashlib.sha256(f.read_bytes()).hexdigest() == digest
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and f.stat().st_uid == 65534
for f in (p,p/'A10',p/'A10/initial'):
    assert stat.S_IMODE(f.stat().st_mode) == 0o700 and f.stat().st_uid == 65534
before = re.findall(r'^([0-9a-f]{64}) (.+)$',(e/'preflight.txt').read_text(),re.M)
after = re.findall(r'^([0-9a-f]{64}) (.+)$',(e/'postflight.txt').read_text(),re.M)
assert before == after and len(after) == 48
for digest,name in after:
    assert hashlib.sha256(Path(name.strip()).read_bytes()).hexdigest() == digest
ledger = json.loads((e.parent/'fixed-task-results.json').read_text())
assert len(ledger['tasks']) == ledger['denominator'] == 30
assert collections.Counter(x['execution'] for x in ledger['tasks']) == dict(
    completed=18,failed=1,waiting_confirmation=2,not_run=6,admission_rejected=3)
assert tuple(ledger['model_tasks_'+k] for k in
             ('completed','failed','waiting_confirmation','not_run','attempted')) == (18,1,2,6,21)
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
assert totals == [35,121+r['provider_attempts'],612991+r['usage']['total_tokens'],96]
for f in (root/'README.md',e/'README.md',e.parent/'acceptance-index.md'):
    for target in re.findall(r'\]\(([^)]+)\)',f.read_text()):
        if '://' not in target and not target.startswith('#'):
            assert (f.parent/target.split('#')[0]).exists(),target
assert all(x in (e/'journal-and-status.txt').read_text() for x in
           ('Result=exit-code','ExecMainStatus=3','ActiveState=failed'))
assert all(x in (e/'postflight.txt').read_text() for x in ('TasksCurrent=0','oom_kill 0','\nactive\n'))
print('PASS A10: insufficient evidence retained as null target, no confirmation or move; human verdict blank')
print('PASS private/public hashes, 48 fingerprints, terminal service and complete ledger:',totals)
