"""Offline integrity check and in-memory negative controls; originals are never edited."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import collect

folder = Path(__file__).resolve().parent
packet = collect.collect()
assert json.loads((folder / 'packet.json').read_text()) == packet
assert (folder / 'packet.md').read_text() == collect.render(packet)
assert len(packet['tasks']) == 30 and len(packet['attempts']) == 37
assert len(packet['citations']) == 96 and packet['latest_attempt_citations'] == 49
assert packet['superseded_attempt_citations'] == 47
assert len({row['fixture'] for row in packet['citations']}) == 11
assert packet['human_reviewed'] == 0 and packet['citation_accuracy'] is None
first_report = next(row for row in packet['citations'] if row['attempt'] == 'H01-05')
assert first_report['pointer'] == '/handover_report/report/items/0/citations/0'
assert first_report['report_item'] == {'key': 'requirements', 'status': 'found'}
assert packet['citations'][0]['report_item'] is None
assert all(row['assessment'] == {'located': None, 'supported': None, 'error': None, 'notes': ''}
           for row in packet['citations'])

# Alter Q01 in memory after checking actual file hashes. Rebuild the in-memory
# review too, so source/denominator checks (not just snapshot-review equality) reject it.
real_read = collect.private_json
def mutate(value):
    value = copy.deepcopy(value)
    if value['case_id'] == 'Q01':
        source = value['citation_sources'][0]
        if mutation == 'missing_source':
            value['citation_sources'] = []
        elif mutation == 'wrong_version':
            source['reference']['version_id'] = '0' * 32
        elif mutation == 'wrong_text':
            source['source']['chunk']['text'] += 'tampered'
        elif mutation == 'wrong_hash':
            source['source']['chunk']['source_sha256'] = '0' * 64
        elif mutation == 'missing_occurrence':
            value['run_result']['result']['citations'] = []
    return value

def altered(path, expected):
    value = real_read(path, expected)
    if path.name == 'snapshot.json':
        return mutate(value)
    if path.name == 'review.json' and value['case_id'] == 'Q01':
        return collect.prepare(mutate(value['snapshot']))
    return value

for mutation in ('missing_source', 'wrong_version', 'wrong_text', 'wrong_hash', 'missing_occurrence'):
    with patch.object(collect, 'private_json', altered):
        try:
            collect.collect()
        except AssertionError:
            pass
        else:
            raise AssertionError('negative control accepted: ' + mutation)
    print('REJECTED in-memory negative control:', mutation)
print('PASS:37 historical attempts/96 occurrences match hashed originals; all 30 tasks retained')
print('PASS:49 latest +47 superseded; 11 fixture versions; no deduplication or business acceptance')
print('PASS:JSON/Markdown regenerated identically; all human assessments blank; zero API calls')
