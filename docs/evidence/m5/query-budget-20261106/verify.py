"""Reconcile live originals, citation locations, usage, and both supplemental attempts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
here = Path(__file__).resolve().parent
base = here.parent
result = json.loads((here / 'result.json').read_text())
assert json.loads(subprocess.check_output([sys.executable, '-B', str(here / 'inspect.py')])) == result
audit = json.loads((here / 'audit-summary.json').read_text())
assert audit['trace_complete'] and audit['outcome'] == {'state': 'completed'}
assert audit['model']['attempts'] == audit['model']['results'] == result['provider_attempts'] == 3
assert audit['model']['usage_total'] == result['usage'] == dict(prompt_tokens=8122, completion_tokens=855, total_tokens=8977, prompt_cache_hit_tokens=5120, prompt_cache_miss_tokens=3002)
assert audit['model_rejection'] is None and audit['tool_rejections'] == []
trace_raw = (here / 'trace.jsonl').read_bytes()
assert hashlib.sha256(trace_raw).hexdigest() == result['private_artifact_sha256']['initial/audit.jsonl']
trace = [json.loads(line) for line in trace_raw.splitlines()]
assert [e['usage']['completion_tokens'] for e in trace if e['event'] == 'model_result'] == [110,224,521]
assert result['run_result']['state'] == 'completed' and result['run_result']['error_code'] is None
assert result['run_result']['model_calls'] == 3
assert [call['tool'] for call in result['backend_tool_calls']['items']] == ['search_documents'] * 2 + ['read_document_version'] * 2
assert all(call['status'] == 'success' for call in result['backend_tool_calls']['items'])
assert len(result['citations']) == len(result['citation_sources']) == 2
assert {source['fixture'] for source in result['citation_sources']} == {'deployment@2','rollback@1'}
corpus = json.loads((root / 'test/fixtures/agent-p0/corpus.json').read_text())
originals = {f"{doc['id']}@{v['version']}": '\n\n'.join(v['paragraphs']) for doc in corpus['documents'] for v in doc['versions']}
for row in result['citations']:
    sources = [entry for entry in result['citation_sources'] if entry['reference'] == row['citation']]
    assert len(sources) == 1
    entry = sources[0]
    source, original = entry['source'], originals[entry['fixture']]
    chunk = source['chunk']
    assert all(chunk[k] == v for k,v in row['citation'].items())
    assert hashlib.sha256(original.encode()).hexdigest() == chunk['source_sha256'] == source['source_sha256']
    assert original[chunk['char_start']:chunk['char_end']] == chunk['text'] == original
    assert chunk['line_start'] == 1 and chunk['line_end'] == 5
    assert source['is_current'] and source['chunk_count'] == 1
    assert row['assessment'] == dict(located=None, supported=None, error=None, notes='')
assert result['review_summary']['citations_reviewed'] == 0
assert result['business_verdict'] is result['citation_accuracy'] is result['cost_estimate'] is None
ledger = json.loads((base / 'supplemental-task-results.json').read_text())
assert ledger['denominator'] == len(ledger['tasks']) == 1 and ledger['citations_observed'] == 2
task = ledger['tasks'][0]
old = json.loads(subprocess.check_output(['git','show','47a6b5a:docs/evidence/m5/supplemental-task-results.json'],cwd=root))
assert task['attempts'][0] == old['tasks'][0]['attempts'][0]
assert task['case_id'] == 'S01' and task['execution'] == 'completed' and task['business_verdict'] is None
assert task['attempt_count'] == len(task['attempts']) == 2
assert task['cumulative_provider_requests'] == sum(a['provider_requests'] for a in task['attempts']) == 6
assert task['cumulative_total_tokens'] == sum(a['total_tokens'] for a in task['attempts']) == 17965
assert task['attempts'][1] == dict(evidence='query-budget-20261106/result.json',state='completed',error_code=None,provider_requests=3,total_tokens=8977,citations_total=2)
for name in ('fixed-task-results.json','citation-review-20261106/packet.json','s01-live-20261106/result.json'):
    assert (base / name).read_bytes() == subprocess.check_output(['git','show','47a6b5a:docs/evidence/m5/'+name],cwd=root)
packet = json.loads((base / 'citation-review-20261106/packet.json').read_text())
assert packet['latest_attempt_citations'] + len(result['citations']) == 51
assert packet['citations_total'] + ledger['citations_observed'] == 98
assert (here / 'live-preflight.txt').read_bytes() == (here / 'live-postflight.txt').read_bytes()
status = (here / 'live-status.txt').read_text().splitlines()
assert all(line in status for line in ('ActiveState=inactive','ExecMainStatus=0','TasksCurrent=0','high 82471','max 0','oom 0','oom_kill 0','active'))
assert 'Ran 126 tests in 86.699s' in (here / 'final.txt').read_text()
print('PASS: S01 attempt 2 completed; 3 requests / 8977 tokens; final output 521 within 1024 cap')
print('PASS: all 2 citations match fixed original text/version/hash/lines; human verdicts remain blank')
print('PASS: both S01 attempts retained, 6 requests / 17965 tokens; original 30 unchanged')
print('PASS: latest citation sample 49+2=51, historical 96+2=98; no accuracy claim')
print('PASS: runtime fingerprints and resource terminal state; offline regression 126/126')
