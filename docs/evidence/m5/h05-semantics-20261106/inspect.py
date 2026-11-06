"""Extract a public H05 observation; never write a human acceptance verdict."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root / 'test/e2e'))
from benchmark_review import prepare, summarize

private = Path('/tmp/smartdocs-m5-h05-semantics.n9xpRJ/H05')
snapshot = json.loads((private / 'initial/snapshot.json').read_text())
review = json.loads((private / 'review.json').read_text())
assert review == prepare(snapshot)
trace = [json.loads(line) for line in (private / 'initial/audit.jsonl').read_text().splitlines()]
models = [event for event in trace if event['event'] == 'model_result']
attempts = [event for event in trace if event['event'] == 'model_attempt']
assert snapshot['case_id'] == 'H05' and snapshot['capture']['complete']
assert len(attempts) <= 6
assert all(event['max_tokens'] == 4096 for event in attempts)
assert snapshot['execution']['automatic_retry'] is False
assert snapshot['execution']['automatic_confirmation'] is False
assert snapshot.get('archive_plan') is None
case = next(case for case in json.loads((root / 'test/fixtures/agent-p0/tasks.json').read_text())['tasks'] if case['id'] == 'H05')
mapping = snapshot['fixture_mapping']
assert set(mapping) == set(case['present']) == set(case['scope']) and len(mapping) == 11
assert all(item['approved'] and item['parse_state'] == 'completed' for item in mapping.values())
calls = snapshot['tool_calls']['items']
report = snapshot['handover_report']
statuses = {item['key']: item['status'] for item in report['report']['items']}
selected = sorted([{key: mapping[ref][key] for key in ('file_id', 'version_id')} for ref in case['scope']], key=lambda item: item['file_id'])
assert report['selected_versions'] == selected
searches = [call for call in calls if call['tool'] == 'search_documents']
reads = [call for call in calls if call['tool'] == 'read_document_version']
search_results = [event for event in trace if event['event'] == 'tool_result' and event['tool'] == 'search_documents']
refs = [citation for item in report['report']['items'] for citation in item['citations']]
usage = {key: sum(event['usage'][key] for event in models) for key in models[0]['usage']}
checks = {
    'state': report['state'], 'selected_version_count': len(selected),
    'source_version_count': len(report['source_versions']),
    'source_dependencies_match_first_ten_file_ids': report['source_versions'] == selected[:10],
    'omitted_fixture_sources': [ref for ref, item in mapping.items() if item['file_id'] not in {v['file_id'] for v in report['source_versions']}],
    'item_statuses': statuses, 'expected_item_statuses': case['expected']['statuses'],
    'expected_item_statuses_match': statuses == case['expected']['statuses'],
    'metadata_only_requirement_met': len(searches) == 1 and searches[0]['parameters']['mode'] == 'metadata' and not reads,
    'search_item_counts': [event.get('item_count') for event in search_results],
    'content_read_calls': len(reads), 'citation_occurrences': len(refs),
    'summary': report['report']['summary'], 'limitations': report['report']['limitations'],
    'raw_pagination_wire_retained': False,
}
result = {
    'case_id': 'H05', 'attempt_number': 3, 'metadata': snapshot['metadata'],
    'run_result': {key: snapshot['run_result'].get(key) for key in ('state', 'error_code', 'model_calls', 'kind')},
    'capture_complete': snapshot['capture']['complete'], 'backend_tool_calls': snapshot['tool_calls'],
    'provider_attempts': len(attempts), 'provider_results': len(models),
    'max_tokens_observed': [event['max_tokens'] for event in attempts], 'usage': usage,
    'tools_by_name': dict(Counter(call['tool'] for call in calls)), 'report_checks': checks,
    'review_summary': summarize(snapshot, review), 'business_verdict': None,
    'citation_accuracy': None, 'cost_estimate': None,
    'fixture_sha256': review['fixture_sha256'],
    'private_artifact_sha256': {name: hashlib.sha256((private / name).read_bytes()).hexdigest() for name in ('initial/snapshot.json', 'initial/audit.jsonl', 'summary.json', 'review.json')},
}
print(json.dumps(result, ensure_ascii=False, indent=2))
