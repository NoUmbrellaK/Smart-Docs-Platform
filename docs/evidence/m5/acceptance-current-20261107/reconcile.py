"""Read-only ledger/private-review reconciliation; no model calls or verdict writes."""
from collections import Counter
import json
from pathlib import Path
import runpy

base = Path('docs/evidence/m5')
# Reuse the existing bounded, hash-checking reader and review contract, not its
# historical collect() entrypoint whose 37-attempt/96-citation assertions are frozen.
helpers = runpy.run_path(str(base / 'citation-review-20261106/collect.py'))
combined = Counter()
for name, denominator in (('fixed-task-results.json', 30), ('supplemental-task-results.json', 1)):
    ledger = json.loads((base / name).read_text())
    tasks = ledger['tasks']
    assert len(tasks) == len({t['case_id'] for t in tasks}) == ledger['denominator'] == denominator
    totals = Counter()
    latest = 0
    for task in tasks:
        assert task['business_verdict'] is None
        attempts = task.get('attempts', [])
        for attempt in attempts:
            saved = json.loads((base / attempt['evidence']).read_text())
            assert saved['run_result']['state'] == attempt['state']
            assert saved['provider_attempts'] == attempt['provider_requests']
            assert saved['usage']['total_tokens'] == attempt['total_tokens']
            folder = Path(saved.get('metadata', {}).get('evidence_path', '/tmp/smartdocs-m5-h01.Mtn9xS/H01'))
            assert folder.is_absolute() and str(folder).startswith('/tmp/smartdocs-m5-')
            hashes = saved.get('private_artifact_sha256', saved.get('private_artifacts'))
            source_key = next(k for k in hashes if k.endswith('initial/snapshot.json'))
            review_key = next(k for k in hashes if k.endswith('review.json'))
            snapshot = helpers['private_json'](folder / 'initial/snapshot.json', hashes[source_key])
            review = helpers['private_json'](folder / 'review.json', hashes[review_key])
            assert review == helpers['prepare'](snapshot)
            assert snapshot['case_id'] == task['case_id'] and snapshot['capture']['complete']
            assert snapshot['run_result']['state'] == attempt['state']
            summary = helpers['summarize'](snapshot, review)
            citations = saved.get('review_summary', {}).get('citations_total', saved.get('citations_total'))
            assert citations == summary['citations_total'] == len(review['citations'])
            assert summary['citations_reviewed'] == 0 and summary['business_verdict'] is None
            totals.update(attempts=1, requests=attempt['provider_requests'],
                          tokens=attempt['total_tokens'], citations=citations)
        if attempts:
            assert len(attempts) == task['attempt_count']
            assert sum(a['provider_requests'] for a in attempts) == task['cumulative_provider_requests']
            assert sum(a['total_tokens'] for a in attempts) == task['cumulative_total_tokens']
            assert task['execution'] == attempts[-1]['state']
            latest += citations
    assert totals['citations'] == ledger['citations_observed']
    totals['latest_citations'] = latest
    combined.update(totals)
    print(name, json.dumps(dict(totals), sort_keys=True))
    print('states', json.dumps(dict(Counter(t['execution'] for t in tasks)), sort_keys=True))
    print('not_run', [t['case_id'] for t in tasks if t['execution'] == 'not_run'])
    print('failed', [t['case_id'] for t in tasks if t['execution'] == 'failed'])
assert dict(combined) == dict(attempts=42, requests=143, tokens=718772, citations=106, latest_citations=59)
print('combined', json.dumps(dict(combined), sort_keys=True))
print('PASS: all 42 private snapshots/reviews match recorded hashes; all human verdicts/assessments remain blank')
print('Scope: metadata and integrity reconciliation only, not source-support/semantic acceptance or a new model run')
