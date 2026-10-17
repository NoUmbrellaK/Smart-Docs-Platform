"""Opt-in resident encoder supervision; private protocol, never an auth endpoint."""
import io
import json
import os
from pathlib import Path
import resource
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
from time import monotonic

from .embedding_service import handle_request
from .embeddings import (ENCODING_ERRORS, MAX_INPUT_BYTES, MAX_OUTPUT_BYTES,
                         EmbeddingError, validate_chunks, validate_result)
from .worker import worker_lock

READY = b'{"ready":true}'


def _error(code):
    return json.dumps({"error": code if code in ENCODING_ERRORS else "embedding_worker_failed"}).encode()


def _busy(listener, request_deadline):
    connection, _ = listener.accept()
    with connection:
        connection.setblocking(False)
        try:
            # Honor half-close framing even if accept precedes the first send.
            # Slow/oversized competing clients are dropped without encoding.
            deadline, total = min(request_deadline, monotonic()+1), 0
            while True:
                _wait(connection, False, deadline)
                part = connection.recv(min(65536, MAX_INPUT_BYTES+1-total))
                if not part: break
                total += len(part)
                if total > MAX_INPUT_BYTES: return
            _send(connection, _error("embedding_busy"), deadline)
            connection.shutdown(socket.SHUT_WR)
        except OSError: pass


def _wait(channel, write, deadline, listener=None):
    while True:
        remaining = deadline-monotonic()
        if remaining <= 0: raise TimeoutError()
        reads = ([] if write else [channel]) + ([listener] if listener else [])
        readable, writable, _ = select.select(reads, [channel] if write else [], [], remaining)
        if listener in readable: _busy(listener, deadline)
        if monotonic() >= deadline: raise TimeoutError()
        if channel in readable or channel in writable: return


def _send(channel, data, deadline, listener=None):
    view = memoryview(data)
    while view:
        _wait(channel, True, deadline, listener)
        sent = channel.send(view)
        if sent == 0: raise EOFError()
        view = view[sent:]


def _receive(channel, count, deadline, listener=None):
    raw = bytearray()
    while len(raw) < count:
        _wait(channel, False, deadline, listener)
        part = channel.recv(count-len(raw))
        if not part: raise EOFError()
        raw.extend(part)
    return bytes(raw)


def _frame(channel, limit, deadline, listener=None):
    size, = struct.unpack("!I", _receive(channel, 4, deadline, listener))
    if not 0 < size <= limit: raise EmbeddingError("embedding_result_too_large")
    return _receive(channel, size, deadline, listener)


def _send_frame(channel, body, deadline, listener=None):
    _send(channel, struct.pack("!I", len(body))+body, deadline, listener)


def _limit(kind, bound):
    soft, hard = resource.getrlimit(kind)
    limit = min(value for value in (soft, hard, bound) if value != resource.RLIM_INFINITY)
    resource.setrlimit(kind, (limit, limit))


def child_main(fd, model_dir):
    from .resident_embeddings import ResidentEncoder
    _limit(resource.RLIMIT_AS, 2*1024**3)
    _limit(resource.RLIMIT_FSIZE, 256*1024**2)
    _limit(resource.RLIMIT_CORE, 0)
    engine = ResidentEncoder(model_dir)
    _limit(resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES)
    with socket.socket(fileno=fd) as channel:
        channel.setblocking(False)
        # Parent enforces startup/request deadlines and kills this process group.
        _send_frame(channel, READY, monotonic()+60)
        while True:
            # Idle lifetime is bounded by service ownership, not a request timer.
            select.select([channel], [], [])
            raw = _frame(channel, MAX_INPUT_BYTES, monotonic()+60)
            output = io.BytesIO()
            handle_request(io.BytesIO(raw), output, engine.encode)
            _send_frame(channel, output.getvalue(), monotonic()+60)


def _child_command(executable, fd, model):
    return [executable, "-B", "-m", "smartdocs.resident_service", "--child", str(fd), model]


def _reap(child):
    if child is not None:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        # A kernel D-state task may not immediately honor SIGKILL. Bound the wait,
        # fail the service, and require an empty cgroup before manual restart.
        child.wait(timeout=5)


def _notify_ready():
    path = os.environ.get("NOTIFY_SOCKET")
    if path:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notification:
            notification.settimeout(1)
            notification.sendto(b"READY=1", "\0"+path[1:] if path.startswith("@") else path)


def _request(connection, deadline, listener):
    raw = bytearray()
    while True:
        _wait(connection, False, deadline, listener)
        part = connection.recv(min(65536, MAX_INPUT_BYTES+1-len(raw)))
        if not part: break
        raw.extend(part)
        if len(raw) > MAX_INPUT_BYTES: raise EmbeddingError("embedding_invalid_input")
    try: chunks = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise EmbeddingError("embedding_invalid_input") from None
    validate_chunks(chunks)
    return bytes(raw), chunks


def _check_reply(raw, chunks):
    try: result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise EmbeddingError("embedding_invalid_result") from None
    if isinstance(result, dict) and set(result) == {"error"}:
        code = result["error"]
        if not isinstance(code, str) or code not in {"embedding_invalid_input", "embedding_window_limit"}:
            raise EmbeddingError(code if isinstance(code, str) and code in ENCODING_ERRORS
                                 else "embedding_worker_failed")
    else: validate_result(result, chunks)


def serve(path, model_dir, python_executable, lock_path, startup_seconds=180, request_seconds=60):
    child, bound = None, None
    def stop(_signum, _frame): raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        if (not 0 < startup_seconds <= 180 or not 0 < request_seconds <= 60
                or not all(isinstance(p, str) and os.path.isabs(p) and "\0" not in p
                           for p in (path, model_dir, python_executable, lock_path))):
            raise EmbeddingError("embedding_invalid_input")
        parent = os.lstat(os.path.dirname(path))
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077 or os.path.lexists(path)):
            raise EmbeddingError("embedding_worker_failed")
        with worker_lock(lock_path):
            try:
                channel, inherited = socket.socketpair()
                with channel, inherited:
                    environment = {"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                        "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "TOKENIZERS_PARALLELISM": "false",
                        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
                    deadline = monotonic()+startup_seconds
                    child = subprocess.Popen(_child_command(python_executable, inherited.fileno(), model_dir),
                        pass_fds=(inherited.fileno(),), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env=environment, start_new_session=True, cwd="/")
                    inherited.close()
                    _limit(resource.RLIMIT_AS, 512*1024**2)
                    _limit(resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES)
                    _limit(resource.RLIMIT_CORE, 0)
                    channel.setblocking(False)
                    if _frame(channel, 128, deadline) != READY:
                        raise EmbeddingError("embedding_worker_failed")
                    with socket.socket(socket.AF_UNIX) as listener:
                        listener.bind(path)
                        info = os.lstat(path); bound = (info.st_dev, info.st_ino)
                        os.chmod(path, 0o600)
                        listener.listen(1)
                        _notify_ready()
                        while True:
                            ready, _, _ = select.select([listener, channel], [], [])
                            if channel in ready: raise EmbeddingError("embedding_worker_failed")
                            connection, _ = listener.accept()
                            with connection:
                                connection.setblocking(False)
                                deadline = monotonic()+request_seconds
                                try:
                                    raw, chunks = _request(connection, deadline, listener)
                                    _send_frame(channel, raw, deadline, listener)
                                    reply = _frame(channel, MAX_OUTPUT_BYTES, deadline, listener)
                                    _check_reply(reply, chunks)
                                    _send(connection, reply, deadline, listener)
                                except EmbeddingError as error:
                                    if error.code == "embedding_invalid_input":
                                        _send(connection, _error(error.code), deadline, listener)
                                        continue
                                    retiring, child = child, None
                                    _reap(retiring)
                                    _send(connection, _error(error.code), monotonic()+1)
                                    return 1
                                except (OSError, EOFError):
                                    code = "embedding_timeout" if monotonic() >= deadline else "embedding_worker_failed"
                                    retiring, child = child, None
                                    _reap(retiring)
                                    try: _send(connection, _error(code), monotonic()+1)
                                    except OSError: pass
                                    return 1
            finally:
                _reap(child)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        code = (error.code if isinstance(error, EmbeddingError) else
                "embedding_timeout" if isinstance(error, TimeoutError) else "embedding_worker_failed")
        print("resident: "+(code if code in ENCODING_ERRORS else "embedding_worker_failed"), file=sys.stderr)
        return 1
    finally:
        if bound is not None:
            try:
                info = os.lstat(path)
                if (info.st_dev, info.st_ino) == bound: os.unlink(path)
            except FileNotFoundError: pass
        signal.signal(signal.SIGTERM, previous)


def main():
    if os.geteuid() == 0: return 1
    for family in (socket.AF_INET, socket.AF_INET6):
        try: probe = socket.socket(family)
        except OSError: continue
        probe.close()
        return 1
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child_main(int(sys.argv[2]), sys.argv[3])
        return 0
    if len(sys.argv) != 5: return 1
    return serve(*sys.argv[1:])


if __name__ == "__main__": raise SystemExit(main())
