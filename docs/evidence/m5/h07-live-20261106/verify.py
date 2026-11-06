"""Cross-check public evidence with private H07 artifacts and the full ledger."""
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
e = root / 'docs/evidence/m5/h07-live-20261106'
p = Path('/tmp/smartdocs-m5-h07-live.40LnB6')
output = io.StringIO()
with contextlib.redirect_stdout(output):
    runpy.run_path(str(e / 'inspect.py'))
result = json.loads((e / 'result.json').read_text())
assert json.loads(output.getvalue()) == result
assert result['run_result'] == dict(state='completed', error_code=None, model_calls=4, kind='handover_report')
assert result['usage']['total_tokens'] == 24516
assert result['dispatched_tools_per_model_response'] == [1,3,3,1]
assert result['report_checks']['expected_item_statuses_match']
assert result['report_checks']['source_versions'] == 6
assert result['report_checks']['unique_citation_sources'] == result['review_summary']['citations_total'] == 6
assert result['review_summary']['citations_reviewed'] == 0
assert result['business_verdict'] is result['citation_accuracy'] is None
trace = (e / 'trace.jsonl').read_bytes()
assert trace == (p / 'H07/initial/audit.jsonl').read_bytes()
audit = json.loads((e / 'audit-summary.json').read_text())
assert audit['model']['usage_total'] == result['usage'] and audit['trace_complete']
assert audit['outcome'] == {'state':'completed'} and audit['tool_rejections'] == []
for name, digest in result['private_artifact_sha256'].items():
    f = p / name
    assert hashlib.sha256(f.read_bytes()).hexdigest() == digest
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and f.stat().st_uid == 65534
for f in (p, p/'H07', p/'H07/initial'):
    assert stat.S_IMODE(f.stat().st_mode) == 0o700 and f.stat().st_uid == 65534
before = re.findall(r'^([0-9a-f]{64}) (.+)$', (e/'preflight.txt').read_text(), re.M)
after = re.findall(r'^([0-9a-f]{64}) (.+)$', (e/'postflight.txt').read_text(), re.M)
assert before == after and len(after) == 41
for digest, name in after:
    f = Path(name.strip())
    assert hashlib.sha256(f.read_bytes()).hexdigest() == digest
ledger = json.loads((e.parent / 'fixed-task-results.json').read_text())
assert len(ledger['tasks']) == ledger['denominator'] == 30
assert collections.Counter(x['execution'] for x in ledger['tasks']) == dict(completed=15,not_run=12,admission_rejected=3)
totals = [0,0,0,0]
for item in ledger['tasks']:
    assert item['business_verdict'] is None
    attempts = item.get('attempts', [])
    if not attempts: continue
    assert len(attempts) == item['attempt_count']
    assert sum(a['provider_requests'] for a in attempts) == item['cumulative_provider_requests']
    assert sum(a['total_tokens'] for a in attempts) == item['cumulative_total_tokens']
    for attempt in attempts:
        saved = json.loads((e.parent / attempt['evidence']).read_text())
        assert saved['run_result']['state'] == attempt['state']
        assert saved['provider_attempts'] == attempt['provider_requests']
        assert saved['usage']['total_tokens'] == attempt['total_tokens']
        totals = [x+y for x,y in zip(totals,[1,attempt['provider_requests'],attempt['total_tokens'],
                                           saved.get('review_summary',{}).get('citations_total',0)])]
assert totals == [29,108,562515,91] and ledger['citations_observed'] == 91
for f in (root/'README.md', e/'README.md', e.parent/'acceptance-index.md'):
    for target in re.findall(r'\]\(([^)]+)\)',f.read_text()):
        if '://' not in target and not target.startswith('#'):
            assert (f.parent / target.split('#')[0]).exists(), target
assert all(x in (e/'journal-and-status.txt').read_text() for x in
           ('Result=success','ExecMainStatus=0','ActiveState=inactive','TasksCurrent=0','oom_kill 0'))
print('PASS H07: 4 observed requests/24516 tokens, 6 found statuses and source locations, draft; human verdict blank')
print('PASS private/public hashes, 41 fingerprint records, terminal service and 30-task ledger: 29/108/562515/91')
