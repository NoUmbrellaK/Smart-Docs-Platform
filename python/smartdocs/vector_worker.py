"""Bounded vector coordinator; C++ authorizes every batch, isolated service encodes it."""
import argparse
from functools import partial
import json
import os
import resource
import socket
import stat
import sys
import time

from .embeddings import (EMBEDDING_REVISION, ENCODING_ERRORS, MAX_INPUT_BYTES,
                         MAX_OUTPUT_BYTES, EmbeddingError, validate_chunks, validate_result)
from .worker import Client, RemoteError, WorkerError, _fail, _hex, worker_lock


class VectorClient(Client):
    def claim(self): return self._request("POST", "/vector/claim")
    def recover(self): return self._request("POST", "/vector/recover")
    def chunks(self, job):
        return self._request("GET", "/vector/"+job["id"]+"/chunks", attempt=job["attempt_token"])
    def publish_batch(self, job, start, result):
        return self._request("POST", "/vector/"+job["id"]+"/result",
                             {"batch_start": start, "result": result}, job["attempt_token"], replay=True)
    def fail(self, job, code):
        result = super().fail(job, code)
        if result != {"state": "failed"}: raise WorkerError("worker_response_invalid")
        return result


def request_encoding(path, chunks):
    """One half-closed Unix stream per batch; never replay expensive encoding automatically."""
    validate_chunks(chunks)
    payload = json.dumps(chunks, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_INPUT_BYTES:
        raise EmbeddingError("embedding_invalid_input")
    try:
        if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
            raise EmbeddingError("embedding_worker_failed")
        info = os.lstat(path)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise EmbeddingError("embedding_worker_failed")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            deadline = time.monotonic() + 75
            connection.settimeout(75)
            connection.connect(path)
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
            raw = bytearray()
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0: raise TimeoutError()
                connection.settimeout(remaining)
                part = connection.recv(min(65536, MAX_OUTPUT_BYTES+1-len(raw)))
                if not part: break
                raw.extend(part)
                if len(raw) > MAX_OUTPUT_BYTES: raise EmbeddingError("embedding_result_too_large")
    except TimeoutError:
        raise EmbeddingError("embedding_timeout") from None
    except OSError:
        raise EmbeddingError("embedding_worker_failed") from None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise EmbeddingError("embedding_invalid_result") from None
    if isinstance(result, dict) and set(result) == {"error"}:
        code = result["error"]
        raise EmbeddingError(code if isinstance(code, str) and code in ENCODING_ERRORS
                             else "embedding_worker_failed")
    validate_result(result, chunks)
    return result


def run_once(client, encoder):
    job = client.claim()
    if job is None: return "idle"
    if (not isinstance(job, dict) or set(job) != {"id", "file_id", "version_id", "attempt_token"}
            or any(not _hex(value, 32) for value in job.values())):
        raise WorkerError("invalid_claim")
    try:
        cursor, count = 0, None
        # ponytail: sequential batches, maximum 250 for the existing 2000-chunk limit.
        for _ in range(250):
            batch = client.chunks(job)
            if (not isinstance(batch, dict)
                    or set(batch) != {"batch_start", "chunk_count", "embedding_revision", "chunks"}
                    or type(batch["batch_start"]) is not int or batch["batch_start"] != cursor
                    or type(batch["chunk_count"]) is not int or not cursor < batch["chunk_count"] <= 2000
                    or (count is not None and count != batch["chunk_count"])
                    or batch["embedding_revision"] != EMBEDDING_REVISION):
                raise EmbeddingError("embedding_invalid_input")
            count = batch["chunk_count"]
            chunks = batch["chunks"]
            validate_chunks(chunks)
            if len(chunks) != min(8, count-cursor): raise EmbeddingError("embedding_invalid_input")
            result = encoder(chunks)
            validate_result(result, chunks)
            if len(json.dumps(result, separators=(",", ":"), allow_nan=False).encode()) > MAX_OUTPUT_BYTES:
                raise EmbeddingError("embedding_result_too_large")
            state = client.publish_batch(job, cursor, result)
            cursor += len(chunks)
            expected = "completed" if cursor == count else "running"
            if state != {"state": expected}: raise WorkerError("worker_response_invalid")
            if cursor == count: return "completed"
        raise EmbeddingError("embedding_window_limit")
    except EmbeddingError as error:
        return _fail(client, job, error.code if error.code in ENCODING_ERRORS else "embedding_worker_failed")
    except RemoteError as error:
        if str(error) == "attempt_conflict" or error.status == 404: return "discarded"
        if error.status in (400, 409, 413):
            code = str(error) if str(error) in {"embedding_window_limit", "version_conflict", "content_unavailable"} else "embedding_worker_failed"
            return _fail(client, job, code)
        raise  # uncertain transport/publication: exit, do not claim or overwrite state


def main(argv=None):
    parser = argparse.ArgumentParser(description="Trusted local vector task coordinator")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() == 0: raise WorkerError("worker_must_be_unprivileged")
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        bound = min([512*1024*1024] + [v for v in (soft, hard) if v != resource.RLIM_INFINITY])
        resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
        client = VectorClient(int(os.environ.get("SMARTDOCS_WORKER_PORT", "1316")), os.environ.get("SMARTDOCS_WORKER_TOKEN", ""))
        encoder = partial(request_encoding, os.environ.get("SMARTDOCS_ENCODER_SOCKET", "/run/smartdocs-encoder/encoder.sock"))
        with worker_lock(os.environ.get("SMARTDOCS_VECTOR_LOCK_PATH", "/run/smartdocs-vector/worker.lock")):
            recovered = client.recover()
            if (not isinstance(recovered, dict) or set(recovered) != {"interrupted_count"}
                    or type(recovered["interrupted_count"]) is not int or recovered["interrupted_count"] < 0):
                raise WorkerError("worker_response_invalid")
            if recovered["interrupted_count"]: print("vector: interrupted_previous_attempts", flush=True)
            while True:
                state = run_once(client, encoder)
                if state != "idle": print("vector: "+state, flush=True)
                if args.once: return 0
                if state == "idle": time.sleep(5)
    except WorkerError as error:
        print("vector: "+str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("vector: internal_error", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
