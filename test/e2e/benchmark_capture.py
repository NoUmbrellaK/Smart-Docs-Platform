"""Read-only snapshots while a frozen fixture is alive. No model or write calls."""
import copy
import hashlib
import http.client
import json
import os
import re

from benchmark_review import FIXTURES, MAX_BYTES, prepare, require


def capture(case_id, prepared, metadata, *, run_kind):
    """Use prepare_case's owner client; never serialize that client or its cookies.

    Sequential GETs are NOT a transactional snapshot. Re-read the observed objects
    to detect changes; any failed read or difference makes the capture unusable for
    review. This does not prove that permissions cannot change after collection.
    """
    manifest = json.loads((FIXTURES / 'tasks.json').read_text())
    require(case_id in {case['id'] for case in manifest['tasks']})
    require(run_kind in ('live_provider', 'transport_fixture', 'admission_rejection'))
    fields = ('code_revision', 'recorded_at', 'environment', 'model', 'evidence_path')
    require(isinstance(metadata, dict) and set(metadata) == set(fields))
    require(all(isinstance(metadata[k], str) and metadata[k].strip() for k in fields))
    result = {'case_id': case_id, 'run_kind': run_kind, 'metadata': copy.deepcopy(metadata),
              'admission_http': prepared['admission_http'], 'run_result': None,
              'fixture_mapping': copy.deepcopy(prepared['mapping']),
              'fixture_sha256': {name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
                                 for name in ('corpus.json', 'tasks.json')},
              'citation_sources': [], 'capture': {'complete': False, 'reads': [], 'error': None}}
    if run_kind == 'admission_rejection':
        require(prepared['task'] is None and prepared['path'] is None
                and prepared['admission_http'] in (403, 404))
        result['capture']['complete'] = True
        prepare(result)
        return result
    path = prepared['path']
    match = re.fullmatch(r'/api/v1/projects/([0-9a-f]{32})/agent-tasks/([0-9a-f]{32})',
                         path if isinstance(path, str) else '')
    require(match is not None and prepared['admission_http'] == 201)
    project, task = match.groups()
    require(prepared['task']['id'] == task and prepared['task']['project_id'] == project)
    observed = {}

    def read(url):
        response = prepared['actor'].request('GET', url)
        result['capture']['reads'].append({'path': url, 'http': response.status})
        require(response.status == 200 and len(response.body) <= MAX_BYTES)
        value = json.loads(response.body)['data']
        require(isinstance(value, dict))
        return value

    def first(url):
        value = read(url)
        observed[url] = copy.deepcopy(value)
        return value

    try:
        state = result['task'] = first(path)
        require(state.get('id') == task and state.get('project_id') == project)
        run = result['run_result'] = first(path + '/run/result')
        require(run.get('state') == state.get('state'))
        trace = result['tool_calls'] = first(path + '/tool-calls')
        require(trace.get('kind') == 'backend_requests' and trace.get('capacity') == 64
                and isinstance(trace.get('items'), list) and len(trace['items']) <= 64)
        # A full buffer alone cannot prove completeness of a historical trace.
        result['trace_at_capacity'] = len(trace['items']) == 64
        if run.get('kind') == 'handover_report':
            report = result['handover_report'] = first(path + '/handover-report')
            require(report.get('task_id') == task and report.get('project_id') == project)
        if 'archive_policy_revision' in state:
            plan = result['archive_plan'] = first(path + '/archive-plan')
            require(plan.get('task_id') == task and plan.get('project_id') == project)
        if run.get('kind') == 'archive_operation':
            receipt = result['archive_receipt'] = first(path + '/archive-plan/result')
            require(receipt == run.get('archive_operation'))
        # Reuse review expansion so repeated references remain in the denominator.
        review = prepare({k: v for k, v in result.items() if k != 'capture'})
        seen = set()
        for row in review['citations']:
            ref = row['citation']
            require(isinstance(ref, dict))
            ids = tuple(ref.get(k) for k in ('file_id', 'version_id', 'chunk_id'))
            require(all(isinstance(v, str) and re.fullmatch('[0-9a-f]{%d}' % n, v)
                        for v, n in zip(ids, (32, 32, 64))))
            if ids in seen:
                continue
            seen.add(ids)
            # Same dependency check as the UI, not just an independently readable file.
            require(read(path + '/run/result') == run)
            url = f'/api/v1/projects/{project}/files/{ids[0]}/versions/{ids[1]}/chunks/{ids[2]}'
            source = first(url)
            require(all(source.get('chunk', {}).get(k) == ref[k]
                        for k in ('file_id', 'version_id', 'chunk_id')))
            require(isinstance(source['chunk'].get('text'), str))
            result['citation_sources'].append({'reference': dict(zip(
                ('file_id', 'version_id', 'chunk_id'), ids)), 'source': source})
        for url, original in observed.items():
            require(read(url) == original)
        require(read(path + '/run/result') == run)
        result['capture']['complete'] = True
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError, RecursionError):
        # Never save exception text, error response bodies, headers or credentials.
        result['capture']['error'] = 'read_failed_invalid_or_changed'
    return result


def write_snapshot(path, snapshot):
    """Keep incomplete captures for diagnosis, exclusively, privately and bounded."""
    raw = (json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode()
    require(len(raw) <= MAX_BYTES)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(raw)
