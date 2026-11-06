"""Extract all S01 output and verify archived locations, never assign human verdicts."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import stat
import sys

root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(root / 'test/e2e'))
from benchmark_review import prepare, summarize

private = Path('/tmp/smartdocs-m5-s01-live.tYfpFa/S01')
names = ('initial/snapshot.json', 'initial/audit.jsonl', 'summary.json', 'review.json')
raw = {}
for name in names:
    path = private / name
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == 65534
    raw[name] = path.read_bytes()
for path in (private.parent, private, private / 'initial'):
    assert stat.S_IMODE(path.stat().st_mode) == 0o700 and path.stat().st_uid == 65534
snapshot = json.loads(raw['initial/snapshot.json'])
review = json.loads(raw['review.json'])
assert review == prepare(snapshot) and review['dataset'] == 'smartdocs-p0-supplemental-v1'
assert snapshot['case_id'] == 'S01' and snapshot['capture']['complete']
assert snapshot['metadata']['code_revision'] == '7756a7e50ae3b59e27a059bc2e564af659a976b3'
assert snapshot['execution']['automatic_retry'] is snapshot['execution']['automatic_confirmation'] is False
assert snapshot.get('archive_plan') is None and snapshot.get('handover_report') is None
assert not (private / 'review-access.json').exists()
assert snapshot['fixture_sha256'] == review['fixture_sha256']
mapping = snapshot['fixture_mapping']
assert set(mapping) == {'deployment@2', 'rollback@1'}
corpus = json.loads((root / 'test/fixtures/agent-p0/corpus.json').read_text())
texts = {f"{doc['id']}@{v['version']}": '\n\n'.join(v['paragraphs']) for doc in corpus['documents'] for v in doc['versions']}
citations = []
for row in review['citations']:
    ref = {k: row['citation'][k] for k in ('file_id', 'version_id', 'chunk_id')}
    sources = [entry['source'] for entry in snapshot['citation_sources'] if entry['reference'] == ref]
    assert len(sources) == 1
    source = sources[0]
    chunk = source['chunk']
    labels = [label for label, item in mapping.items() if all(item[k] == ref[k] for k in ('file_id', 'version_id'))]
    assert len(labels) == 1
    label = labels[0]
    original = texts[label]
    assert mapping[label]['approved'] and mapping[label]['parse_state'] == 'completed'
    assert all(chunk[k] == v for k, v in ref.items())
    assert hashlib.sha256(original.encode()).hexdigest() == chunk['source_sha256'] == source['source_sha256'] == mapping[label]['sha256']
    assert original[chunk['char_start']:chunk['char_end']] == chunk['text']
    assert chunk['line_start'] == original[:chunk['char_start']].count('\n') + 1
    assert chunk['line_end'] == original[:chunk['char_end']].count('\n') + 1
    citations.append({**row, 'fixture': label, 'archived_location_verified': True, 'source': source})
trace = [json.loads(line) for line in raw['initial/audit.jsonl'].splitlines()]
attempts = [event for event in trace if event['event'] == 'model_attempt']
models = [event for event in trace if event['event'] == 'model_result']
assert 1 <= len(attempts) <= 4 and len(attempts) == len(models)
assert all(event['max_tokens'] == 512 for event in attempts)
assert all(event['usage'] is not None for event in models)
usage = {key: sum(event['usage'][key] for event in models) for key in models[0]['usage']}
calls = snapshot['tool_calls']
assert all(call['tool'] in ('search_documents', 'read_document_version') for call in calls['items'])
print(json.dumps({'case_id': 'S01', 'attempt_number': 1, 'metadata': snapshot['metadata'],
    'dataset': review['dataset'], 'input': review['input'], 'expected': review['expected'],
    'run_result': snapshot['run_result'], 'capture_complete': True,
    'backend_tool_calls': calls, 'provider_attempts': len(attempts), 'provider_results': len(models),
    'max_tokens_observed': [event['max_tokens'] for event in attempts], 'usage': usage,
    'tools_by_name': dict(Counter(call['tool'] for call in calls['items'])), 'citations': citations,
    'review_summary': summarize(snapshot, review), 'business_verdict': None, 'citation_accuracy': None,
    'cost_estimate': None, 'fixture_sha256': review['fixture_sha256'],
    'private_artifact_sha256': {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}},
    ensure_ascii=False, indent=2))
