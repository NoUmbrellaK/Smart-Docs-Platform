"""One prepared frozen case -> existing Agent -> private capture, without retries.

Trusted local harness, not an HTTP endpoint or an authorization to call a provider.
The caller owns the fixture lifetime and a private output parent. Never pass the
prepared mapping, expected answers, or cookies to the model. No archive confirmation.
"""
import json
from pathlib import Path
import re
import tempfile

from benchmark_capture import capture, write_snapshot
from benchmark_review import FIXTURES, require
from smartdocs import agent_cli


async def execute(case_id, prepared, metadata, output_dir, *, backend_port,
                  api_key, max_calls, run_kind, encoder_socket=None):
    """Return a captured outcome, not a business verdict.

    Calls run_context at most once. A failed/uncertain run is captured without
    retrying or cancelling it. Caller must keep a waiting archive fixture alive
    for separate, real human review; this function neither confirms nor resumes.
    run_kind is a caller assertion, not proof of provider execution.
    """
    cases = json.loads((FIXTURES / 'tasks.json').read_text())['tasks']
    case = next((item for item in cases if item['id'] == case_id), None)
    require(case is not None and prepared['body']['question'] == case['input'])
    require(type(max_calls) is int and 1 <= max_calls <= 8)
    require(type(backend_port) is int and 1 <= backend_port <= 65535)
    require(backend_port == prepared['actor'].port)
    fields = {'code_revision', 'recorded_at', 'environment', 'model', 'evidence_path'}
    require(isinstance(metadata, dict) and set(metadata) == fields)
    require(all(isinstance(value, str) and value.strip() for value in metadata.values()))
    expected_http = {'Q08': 404, 'Q09': 404, 'A09': 403}.get(case_id, 201)
    require(prepared['admission_http'] == expected_http)
    rejected = expected_http != 201
    if rejected:
        require(run_kind == 'admission_rejection')
        snapshot = capture(case_id, prepared, metadata, run_kind=run_kind)
    else:
        require(run_kind in {'live_provider', 'transport_fixture'})
        task, path = prepared['task'], prepared['path']
        require(isinstance(task, dict) and isinstance(path, str))
        match = re.fullmatch(r'/api/v1/projects/([0-9a-f]{32})/agent-tasks/([0-9a-f]{32})', path)
        require(match is not None and match.groups() == (task['project_id'], task['id']))
        # Fail before consuming a budget on a reused/mislabeled task. The runner
        # still performs the authoritative claim and all permission checks.
        response = prepared['actor'].request('GET', path)
        require(response.status == 200)
        current = response.json()['data']
        # State GET intentionally omits question text. The fixture creation body
        # above binds the case; compare the server's IDs/scope, not private text.
        require(current['state'] == 'pending' and current['id'] == task['id']
                and current['project_id'] == task['project_id']
                and current['versions'] == task['versions'])
        session = next(cookie.value for cookie in prepared['actor'].cookies
                       if cookie.name == 'smartdocs_session')
        context = {'backend_port': backend_port, 'origin': f'http://127.0.0.1:{backend_port}',
                   'session_token': session, 'project_id': task['project_id'],
                   'task_id': task['id'], 'scope': 'task_versions'}
    output = Path(output_dir)
    output.mkdir(mode=0o700)  # No overwrite/reuse, including symlinks; before any run.
    outcome = None
    if not rejected:
        with tempfile.TemporaryDirectory(prefix='session-', dir=output) as folder:
            context_path = Path(folder) / 'context.json'
            write_snapshot(context_path, context)
            try:
                outcome = await agent_cli.run_context(context_path, output / 'audit.jsonl',
                    api_key, max_calls=max_calls, encoder_socket=encoder_socket)
            except Exception:
                # Never retain exception text (it may contain credentials).
                outcome = {'state': 'unknown', 'error_code': 'dependency_failed'}
        snapshot = capture(case_id, prepared, metadata, run_kind=run_kind)
    snapshot['execution'] = {'outcome': agent_cli.safe_outcome(outcome) if outcome else None,
                             'max_model_calls': max_calls if not rejected else 0,
                             'automatic_retry': False, 'automatic_confirmation': False}
    write_snapshot(output / 'snapshot.json', snapshot)
    return snapshot
