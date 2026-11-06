"""Verify saved observations against private originals; no model/network calls."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys

root = Path.cwd()
base = root / 'docs/evidence/m5'
evidence = base / 'h05-semantics-20261106'
private = Path('/tmp/smartdocs-m5-h05-semantics.n9xpRJ/H05')
value = json.loads((evidence / 'result.json').read_text())
assert json.loads(subprocess.check_output([sys.executable, '-B', str(evidence / 'inspect.py')])) == value
audit = json.loads((evidence / 'audit-summary.json').read_text())
trace = (evidence / 'trace.jsonl').read_bytes()
assert trace == (private / 'initial/audit.jsonl').read_bytes()
assert audit['trace_complete'] and audit['outcome'] == {'state': 'completed'}
assert audit['model']['attempts'] == audit['model']['results'] == 2
assert audit['model']['usage_total'] == value['usage'] == dict(prompt_tokens=7621, completion_tokens=1261, total_tokens=8882, prompt_cache_hit_tokens=2560, prompt_cache_miss_tokens=5061)
assert audit['tool_rejections'] == [] and audit['model_rejection'] is None
assert value['provider_attempts'] == value['provider_results'] == value['run_result']['model_calls'] == 2
assert value['run_result'] == dict(state='completed', error_code=None, model_calls=2, kind='handover_report')
assert value['tools_by_name'] == {'search_documents': 1, 'save_handover_report': 1}
assert [item['tool'] for item in value['backend_tool_calls']['items']] == ['search_documents', 'save_handover_report']
assert all(item['status'] == 'success' for item in value['backend_tool_calls']['items'])
checks = value['report_checks']
assert checks['state'] == 'draft' and checks['search_item_counts'] == [10]
assert (checks['selected_version_count'], checks['source_version_count']) == (11, 10)
assert checks['source_dependencies_match_first_ten_file_ids'] and checks['omitted_fixture_sources'] == ['conflict_b@1']
assert checks['metadata_only_requirement_met'] and checks['expected_item_statuses_match']
assert set(checks['item_statuses'].values()) == {'uncertain'} and len(checks['item_statuses']) == 6
assert checks['content_read_calls'] == checks['citation_occurrences'] == 0
assert '未读取任何文档正文' in checks['summary'] and '不构成内容核查' in checks['summary']
assert '不翻页' in checks['limitations'] and '未核查项属于不确定' in checks['limitations']
assert checks['raw_pagination_wire_retained'] is False
assert value['review_summary']['citations_total'] == value['review_summary']['citations_reviewed'] == 0
assert value['business_verdict'] is value['citation_accuracy'] is value['cost_estimate'] is None
for name, digest in value['private_artifact_sha256'].items():
    path = private / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_uid == 65534
for path in (private.parent, private, private / 'initial'):
    assert stat.S_IMODE(path.stat().st_mode) == 0o700 and path.stat().st_uid == 65534
assert (evidence / 'preflight.txt').read_bytes() == (evidence / 'postflight.txt').read_bytes()
assert subprocess.check_output([sys.executable, '-B', str(evidence / 'fingerprints.py')]) == (evidence / 'postflight.txt').read_bytes()
assert subprocess.check_output(['git', 'diff', 'b6670d687cb685b49c2c00342fa09feffb8f3e7e', '--', 'python/smartdocs/agent_runner.py']) == (evidence / 'candidate.patch').read_bytes()
for name, digest in value['fixture_sha256'].items():
    assert hashlib.sha256((root / 'test/fixtures/agent-p0' / name).read_bytes()).hexdigest() == digest
offline = (evidence / 'offline-journal.txt').read_text()
assert 'Ran 125 tests in 85.852s' in offline and ': OK' in offline
status = (evidence / 'journal-and-status.txt').read_text()
assert all(line in status.splitlines() for line in ('Result=success', 'ExecMainStatus=0', 'ActiveState=inactive', 'TasksCurrent=0', 'high 82471', 'max 0', 'oom 0', 'oom_kill 0', 'active'))
ledger = json.loads((base / 'fixed-task-results.json').read_text())
assert len(ledger['tasks']) == len({item['case_id'] for item in ledger['tasks']}) == ledger['denominator'] == 30
assert Counter(item['execution'] for item in ledger['tasks']) == {'completed': 18, 'failed': 1, 'waiting_confirmation': 3, 'not_run': 5, 'admission_rejected': 3}
totals = [0, 0, 0, 0]
for item in ledger['tasks']:
    assert item['business_verdict'] is None
    history = item.get('attempts', [])
    if not history:
        continue
    assert len(history) == item['attempt_count']
    assert sum(attempt['provider_requests'] for attempt in history) == item['cumulative_provider_requests']
    assert sum(attempt['total_tokens'] for attempt in history) == item['cumulative_total_tokens']
    for key in ('evidence', 'provider_requests', 'total_tokens'):
        assert item[key] == history[-1][key]
    for attempt in history:
        saved = json.loads((base / attempt['evidence']).read_text())
        assert saved['run_result']['state'] == attempt['state']
        assert saved['usage']['total_tokens'] == attempt['total_tokens']
        assert saved['provider_attempts'] == attempt['provider_requests']
        totals = [a + b for a, b in zip(totals, (1, attempt['provider_requests'], attempt['total_tokens'], saved.get('review_summary', {}).get('citations_total', 0)))]
assert totals == [37, 127, 634573, 96], totals
assert ledger['citations_observed'] == 96 and ledger['business_completion_rate'] is ledger['citation_accuracy'] is None
h05 = next(item for item in ledger['tasks'] if item['case_id'] == 'H05')
assert (h05['attempt_count'], h05['cumulative_provider_requests'], h05['cumulative_total_tokens']) == (3, 10, 63718)
assert h05['metadata_only_requirement_met'] and h05['expected_item_statuses_match']
for path in (root / 'README.md', base / 'acceptance-index.md', evidence / 'README.md'):
    for link in re.findall(r'\]\(([^)]+)\)', path.read_text()):
        if '://' not in link and not link.startswith('#'):
            assert (path.parent / link.split('#')[0]).exists(), link
for path in evidence.iterdir():
    if path.is_file():
        assert not re.search(r'sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----', path.read_text()), path
print('PASS: private originals/public extraction agree; blank human review; 2 requests/8882 tokens')
print('PASS: metadata search then save only; ten dependencies/eleven selected; six uncertain; zero body reads/citations')
print('PASS: 47 stable runtime fingerprints and frozen inputs; baseline plus exact four-line patch; offline125/125')
print('PASS: 30-case denominator preserved; history37 attempts/127 requests/634573 tokens/96 citations')
print('PASS: clean terminal state/resource evidence, private artifact permissions, links and secret scan')
