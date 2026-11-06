"""Reproduce public extraction and reconcile failure, usage, and unchanged baseline."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
base = root / 'docs/evidence/m5'
here = Path(__file__).resolve().parent
value = json.loads((here / 'result.json').read_text())
assert json.loads(subprocess.check_output([sys.executable, '-B', str(here / 'inspect.py')])) == value
audit = json.loads((here / 'audit-summary.json').read_text())
trace = [json.loads(line) for line in (here / 'trace.jsonl').read_text().splitlines()]
assert hashlib.sha256((here / 'trace.jsonl').read_bytes()).hexdigest() == value['private_artifact_sha256']['initial/audit.jsonl']
assert audit['trace_complete'] and audit['outcome'] == {'state': 'failed', 'error_code': 'model_invalid'}
assert audit['model']['attempts'] == audit['model']['results'] == value['provider_attempts'] == 3
assert audit['model']['usage_total'] == value['usage'] == dict(prompt_tokens=8134, completion_tokens=854, total_tokens=8988, prompt_cache_hit_tokens=3968, prompt_cache_miss_tokens=4166)
assert audit['model_rejection']['finish_reason'] == 'length' and audit['model_rejection']['sequence'] == 3
assert audit['tool_rejections'] == []
assert [e['usage']['completion_tokens'] for e in trace if e['event'] == 'model_result'] == [111, 231, 512]
assert value['run_result'] == dict(state='failed', error_code='model_invalid', model_calls=3, result=None)
assert value['tools_by_name'] == {'search_documents': 2, 'read_document_version': 2}
assert all(item['status'] == 'success' for item in value['backend_tool_calls']['items'])
assert value['citations'] == [] and value['review_summary']['citations_total'] == 0
assert value['business_verdict'] is value['citation_accuracy'] is value['cost_estimate'] is None
ledger = json.loads((base / 'supplemental-task-results.json').read_text())
assert ledger['denominator'] == len(ledger['tasks']) == 1
assert ledger['manifest_sha256'] == value['fixture_sha256']['supplemental-tasks.json']
task = ledger['tasks'][0]
assert task['case_id'] == 'S01' and task['execution'] == 'failed' and task['business_verdict'] is None
assert task['attempt_count'] == len(task['attempts']) == 1
assert task['attempts'][0] == dict(evidence='s01-live-20261106/result.json', state='failed', error_code='model_invalid', provider_requests=3, total_tokens=8988, citations_total=0)
assert task['cumulative_provider_requests'] == 3 and task['cumulative_total_tokens'] == 8988
for name in ('fixed-task-results.json', 'citation-review-20261106/packet.json'):
    assert (base / name).read_bytes() == subprocess.check_output(['git', 'show', '7756a7e:docs/evidence/m5/' + name], cwd=root)
assert (here / 'preflight.txt').read_bytes() == (here / 'postflight.txt').read_bytes()
status = (here / 'journal-and-status.txt').read_text().splitlines()
assert all(line in status for line in ('ActiveState=failed', 'ExecMainStatus=1', 'TasksCurrent=0', 'high 82471', 'max 0', 'oom 0', 'oom_kill 0', 'active'))
print('PASS S01 failure retained: 3 requests / 8988 tokens / 0 citations; response 3 reached 512 output tokens')
print('PASS separate supplemental denominator 1; original 30 and historical packet unchanged; no human verdict')
print('PASS private hashes/modes, runtime fingerprints, and resource terminal state')
