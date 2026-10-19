"""Private same-origin Agent execution adapter. Not a public server or automatic scheduler."""
import argparse
import asyncio
import http.client
import json
import logging
import os
from pathlib import Path
import resource
import signal
import stat
import tempfile
import threading
import uuid

from .agent_cli import run_context, safe_outcome
from .deepseek import DeepSeekModel
from .search_http import SearchError, SearchHandler, SearchHTTPServer, strict_json


def private_directory(path):
    path = Path(path)
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
            raise ValueError()
        return path.absolute()
    except (OSError, ValueError):
        raise ValueError('private service directory required') from None


class AgentHTTPServer(SearchHTTPServer):
    daemon_threads = False  # server_close drains admitted runs and their private context cleanup.
    busy_response = b'{"error":{"code":"agent_busy","message":"Agent busy; inspect task state before retrying.","retryable":true}}'

    def __init__(self, port, backend_port, origin, runtime_dir, audit_dir, api_key, *, encoder_socket=None, max_model_calls=8):
        DeepSeekModel(api_key, lambda event: None, max_calls=max_model_calls)
        self.runtime_dir, self.audit_dir = private_directory(runtime_dir), private_directory(audit_dir)
        self.api_key, self.encoder_socket, self.max_model_calls = api_key, encoder_socket, max_model_calls
        super().__init__(port, backend_port, origin, None, handler=AgentHandler)

    def task_state(self, cookie, project, task):
        connection = http.client.HTTPConnection('127.0.0.1', self.backend_port, timeout=15)
        try:
            connection.request('GET', f'/api/v1/projects/{project}/agent-tasks/{task}', headers={
                'Host': self.host, 'Origin': self.origin, 'Cookie': cookie, 'Connection': 'close'})
            response = connection.getresponse()
            if response.status != 200:
                codes = {400:'invalid_request', 401:'authentication_required', 403:'forbidden',
                         404:'not_found', 409:'conflict', 429:'agent_busy'}
                raise SearchError(response.status if response.status in codes else 503,
                                  codes.get(response.status, 'agent_backend_unavailable'))
            raw = response.read(65537)
            if len(raw) > 65536: raise ValueError()
            payload = strict_json(raw)
            value = payload.get('data') if isinstance(payload, dict) else None
            if (not isinstance(value, dict) or value.get('id') != task or value.get('project_id') != project
                    or value.get('state') not in {'pending','running','completed','failed','cancelled','interrupted',
                                                  'waiting_confirmation','waiting_execution'}):
                raise ValueError()
            return value['state']
        except (OSError, http.client.HTTPException, ValueError, RecursionError):
            raise SearchError(503, 'agent_backend_unavailable') from None
        finally:
            connection.close()

    def execute(self, cookie, project, task):
        if self.task_state(cookie, project, task) != 'pending':
            raise SearchError(409, 'task_not_pending')
        context = {'backend_port': self.backend_port, 'origin': self.origin,
            'session_token': cookie.partition('=')[2], 'project_id': project, 'task_id': task, 'scope': 'task_versions'}
        # No in-memory queue or retry: the C++ atomic run claim is the duplicate-execution boundary.
        with tempfile.TemporaryDirectory(prefix='task-', dir=self.runtime_dir) as directory:
            path = Path(directory)/'context.json'
            fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(context, output)
            audit = self.audit_dir/(task+'.'+uuid.uuid4().hex+'.jsonl')
            outcome = asyncio.run(run_context(path, audit, self.api_key, self.max_model_calls,
                                              encoder_socket=self.encoder_socket))
        self.task_state(cookie, project, task)  # Revocation/logout during execution cannot disclose an old outcome.
        return safe_outcome(outcome)


class AgentHandler(SearchHandler):
    def do_POST(self):
        try:
            try:
                route, cookie, command = self.read_command(
                    r'/api/v1/projects/([0-9a-f]{32})/agent-tasks/([0-9a-f]{32})/execute')
                if type(command) is not dict or command: raise ValueError()
            except (ValueError, UnicodeError, RecursionError):
                raise SearchError(400, 'invalid_request') from None
            except TimeoutError:
                raise SearchError(408, 'request_timeout') from None
            outcome = self.server.execute(cookie, route[1], route[2])
            self.reply(200, {'data': outcome})
        except SearchError as error:
            self.reply(error.status, {'error': {'code': error.code, 'message': 'Agent request rejected. Refresh task status.',
                                              'retryable': error.status in (429, 503)}})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            # Failure may occur after a claim or commit. Never infer failure or retry a write here.
            self.reply(503, {'error': {'code': 'agent_outcome_unknown',
                'message': 'Refresh task status. No automatic retry.', 'retryable': False}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=1318)
    parser.add_argument('--backend-port', type=int, default=1316)
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--audit-dir', required=True)
    parser.add_argument('--max-model-calls', type=int, choices=range(1,9), default=8)
    args = parser.parse_args()
    if os.geteuid() == 0: parser.error('run as an unprivileged service account')
    logging.disable(logging.CRITICAL)
    key = os.environ.pop('DEEPSEEK_API_KEY', '')
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        with AgentHTTPServer(args.port, args.backend_port, os.environ.get('SMARTDOCS_PUBLIC_ORIGIN', ''),
                args.runtime_dir, args.audit_dir, key, encoder_socket=os.environ.get('SMARTDOCS_ENCODER_SOCKET'),
                max_model_calls=args.max_model_calls) as server:
            # shutdown() must not run on the serve_forever thread (it would deadlock).
            previous = signal.signal(signal.SIGTERM, lambda *_:
                threading.Thread(target=server.shutdown, daemon=True).start())
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
                signal.signal(signal.SIGTERM, previous)
    except (ValueError, OSError):
        print('agent_http: configuration or listener unavailable', file=__import__('sys').stderr)
        return 2
    return 0


if __name__ == '__main__': raise SystemExit(main())
