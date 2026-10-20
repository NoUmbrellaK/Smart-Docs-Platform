"""Offline human review of one frozen case; never runs models or confirms writes."""
import argparse
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import stat


FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/agent-p0'
ERRORS = {'wrong_source', 'wrong_location', 'unsupported_claim', 'unavailable_source', 'other'}
MAX_BYTES = 2 * 1024 * 1024


def require(condition):
    if not condition:
        raise ValueError('invalid review input')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def prepare(source):
    require(isinstance(source, dict))
    if 'capture' in source:
        require(isinstance(source['capture'], dict) and source['capture'].get('complete') is True)
    manifest = json.loads((FIXTURES / 'tasks.json').read_text())
    case = next((case for case in manifest['tasks'] if case['id'] == source.get('case_id')), None)
    require(case is not None)
    require(source.get('run_kind') in {'live_provider', 'admission_rejection', 'transport_fixture', 'not_run'})
    metadata = source.get('metadata')
    require(isinstance(metadata, dict) and all(isinstance(metadata.get(key), str) and metadata[key].strip()
            for key in ('code_revision', 'recorded_at', 'environment', 'model', 'evidence_path')))
    run = source.get('run_result')
    require(run is None or isinstance(run, dict))
    if source['run_kind'] == 'admission_rejection':
        require(run is None and type(source.get('admission_http')) is int
                and source['admission_http'] in (403, 404))
    elif source['run_kind'] == 'not_run':
        require(run is None and source.get('handover_report') is None)
    else:
        require(isinstance(run, dict) and isinstance(run.get('state'), str))
    if run and run.get('state') == 'completed' and run.get('kind') not in ('handover_report', 'archive_operation'):
        require(isinstance(run.get('result'), dict))
    citations = []

    def append(values, pointer, claim):
        require(isinstance(values, list) and isinstance(claim, str))
        for index, citation in enumerate(values):
            # Do not deduplicate repeated references or discard malformed ones: both need review.
            citations.append({'pointer': pointer + '/' + str(index), 'claim': claim,
                'citation': copy.deepcopy(citation),
                'assessment': {'located': None, 'supported': None, 'error': None, 'notes': ''}})

    if run and run.get('result') is not None:
        result = run['result']
        require(isinstance(result, dict))
        append(result.get('citations'), '/run_result/result/citations', result.get('answer'))
    report = source.get('handover_report')
    if run and run.get('kind') == 'handover_report':
        require(isinstance(report, dict))
    if report is not None:
        require(run is not None and run.get('kind') == 'handover_report')
        require(isinstance(report, dict) and isinstance(report.get('report'), dict))
        receipt = run.get('handover_report')
        fields = {'id', 'version', 'url', 'markdown_url'}
        require(isinstance(receipt, dict) and set(receipt) == fields)
        require(type(report.get('version')) is int and type(receipt['version']) is int)
        require(all(isinstance(receipt[key], str) and receipt[key] for key in fields - {'version'}))
        require(all(report.get(key) == receipt[key] for key in fields))
        items = report['report'].get('items')
        require(isinstance(items, list))
        for index, item in enumerate(items):
            require(isinstance(item, dict))
            append(item.get('citations'), f'/handover_report/report/items/{index}/citations', item.get('notes'))
    return {'schema_version': 1, 'case_id': case['id'], 'category': case['category'],
        'dataset': manifest['dataset'], 'input': case['input'], 'expected': copy.deepcopy(case['expected']),
        'fixture_sha256': {name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
                           for name in ('corpus.json', 'tasks.json')},
        'snapshot_sha256': digest(source), 'snapshot': copy.deepcopy(source), 'citations': citations,
        'business': {'verdict': None, 'reviewer': '', 'notes': ''}}


def summarize(source, review):
    expected = prepare(source)
    require(isinstance(review, dict) and set(review) == set(expected))
    require(all(review[key] == value for key, value in expected.items() if key not in ('citations', 'business')))
    rows = review['citations']
    require(isinstance(rows, list) and len(rows) == len(expected['citations']))
    reviewed = correct = 0
    errors = Counter()
    for row, original in zip(rows, expected['citations']):
        require(isinstance(row, dict) and set(row) == set(original))
        require(all(row[key] == original[key] for key in ('pointer', 'claim', 'citation')))
        assessment = row['assessment']
        require(isinstance(assessment, dict) and set(assessment) == set(original['assessment']))
        if assessment == original['assessment']:
            continue
        located, supported = assessment['located'], assessment['supported']
        require(type(located) is bool and type(supported) is bool)
        require(isinstance(assessment['notes'], str) and bool(assessment['notes'].strip()))
        valid = located and supported
        require(assessment['error'] is None if valid else assessment['error'] in ERRORS)
        reviewed += 1
        correct += int(valid)
        if not valid:
            errors[assessment['error']] += 1
    business = review['business']
    require(isinstance(business, dict) and set(business) == set(expected['business']))
    verdict = business['verdict']
    require(verdict in (None, 'pass', 'fail', 'not_run'))
    require(all(isinstance(business[key], str) for key in ('reviewer', 'notes')))
    if verdict is not None:
        require(bool(business['reviewer'].strip()) and bool(business['notes'].strip()))
    if verdict == 'pass':
        require(source['run_kind'] != 'not_run' and reviewed == len(rows) and correct == len(rows))
    return {'case_id': expected['case_id'], 'category': expected['category'],
        'declared_run_kind': source['run_kind'], 'business_verdict': verdict,
        'live_provider_declared': source['run_kind'] == 'live_provider',
        'execution_verified': False, 'citations_total': len(rows), 'citations_reviewed': reviewed,
        'citations_correct': correct, 'citation_errors': dict(errors),
        'citation_accuracy': correct / len(rows) if rows and reviewed == len(rows) else None,
        'snapshot_sha256': expected['snapshot_sha256']}


def read_private(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError('invalid JSON constant')

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_uid == os.getuid() and info.st_size <= MAX_BYTES)
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'summarize'))
    parser.add_argument('snapshot', help='owned regular 0600 snapshot; no credentials')
    parser.add_argument('review', help='new review for prepare; completed review for summarize')
    args = parser.parse_args()
    try:
        source = read_private(args.snapshot)
        if args.command == 'prepare':
            review = prepare(source)
            raw = (json.dumps(review, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode()
            require(len(raw) <= MAX_BYTES)
            descriptor = os.open(args.review, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                output.write(raw)
            result = {'case_id': review['case_id'], 'citations_total': len(review['citations']),
                      'business_verdict': None, 'review_created': True}
        else:
            result = summarize(source, read_private(args.review))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print(json.dumps({'error': 'review_unavailable_or_invalid'}))
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
