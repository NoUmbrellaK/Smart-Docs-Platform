"""Read-only extraction of S01 attempt 2; no automatic semantic verdict."""
import hashlib
import json
from pathlib import Path
import stat
import sys

root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(root / 'test/e2e'))
from benchmark_review import prepare, summarize

private = Path('/tmp/smartdocs-m5-s01-budget.sKbXrz/S01')
raw = {}
for name in ('initial/snapshot.json', 'initial/audit.jsonl', 'summary.json', 'review.json'):
    path = private / name
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == 65534
    raw[name] = path.read_bytes()
for path in (private.parent, private, private / 'initial'):
    assert stat.S_IMODE(path.stat().st_mode) == 0o700 and path.stat().st_uid == 65534
snapshot, review = json.loads(raw['initial/snapshot.json']), json.loads(raw['review.json'])
assert review == prepare(snapshot) and snapshot['case_id'] == 'S01' and snapshot['capture']['complete']
assert snapshot['metadata']['code_revision'] == '0df877ef58ae4b4eb557e2df31b19db0d1c69b0c'
assert snapshot['fixture_sha256'] == review['fixture_sha256']
assert snapshot['execution']['automatic_retry'] is snapshot['execution']['automatic_confirmation'] is False
assert snapshot.get('archive_plan') is None and snapshot.get('handover_report') is None
assert not (private / 'review-access.json').exists()
mapping = snapshot['fixture_mapping']
assert set(mapping) == {'deployment@2', 'rollback@1'}
assert all(item['approved'] and item['parse_state'] == 'completed' for item in mapping.values())
trace = [json.loads(line) for line in raw['initial/audit.jsonl'].splitlines()]
attempts = [event for event in trace if event['event'] == 'model_attempt']
models = [event for event in trace if event['event'] == 'model_result']
assert 1 <= len(attempts) <= 4 and len(models) == len(attempts)
assert all(event['max_tokens'] == 1024 for event in attempts)
assert all(event['usage'] is not None for event in models)
calls = snapshot['tool_calls']
assert all(call['tool'] in ('search_documents', 'read_document_version') for call in calls['items'])
sources = []
for entry in snapshot['citation_sources']:
    labels = [label for label, item in mapping.items() if all(item[k] == entry['reference'][k] for k in ('file_id', 'version_id'))]
    assert len(labels) == 1
    sources.append({**entry, 'fixture': labels[0]})
print(json.dumps({'case_id': 'S01', 'attempt_number': 2, 'dataset': review['dataset'],
    'metadata': snapshot['metadata'], 'input': review['input'], 'expected': review['expected'],
    'run_result': snapshot['run_result'], 'capture_complete': True, 'backend_tool_calls': calls,
    'provider_attempts': len(attempts), 'provider_results': len(models),
    'max_tokens_observed': [event['max_tokens'] for event in attempts],
    'usage': {key: sum(event['usage'][key] for event in models) for key in models[0]['usage']},
    'citations': review['citations'], 'citation_sources': sources,
    'review_summary': summarize(snapshot, review), 'business_verdict': None, 'citation_accuracy': None,
    'cost_estimate': None, 'fixture_sha256': review['fixture_sha256'],
    'private_artifact_sha256': {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}},
    ensure_ascii=False, indent=2))
