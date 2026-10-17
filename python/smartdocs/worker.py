"""One trusted local parser worker. No model keys, arbitrary URLs, or file paths from jobs."""
import argparse
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import http.client
import json
import os
import re
import resource
import stat
import sys
import time

from .text_parser import ParseError, parse_text

PREFIX = "/internal/v1/processing"
MAX_INPUT = 8 * 1024 * 1024
MAX_RESULT = 16 * 1024 * 1024
SAFE_REMOTE_CODES = {"attempt_conflict", "version_conflict", "content_unavailable",
                     "input_too_large", "unsupported_format", "source_hash_mismatch", "invalid_result",
                     "embedding_window_limit", "batch_conflict", "result_conflict"}


class WorkerError(RuntimeError):
    """Only application-owned safe codes; never put responses, secrets, or source text here."""


class RemoteError(WorkerError):
    def __init__(self, status, code="worker_http_error"):
        super().__init__(code if code in SAFE_REMOTE_CODES else "worker_http_error")
        self.status = status


def _hex(value, size):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % size, value) is not None


def _claim(value):
    if value is None:
        return None
    fields = {"id", "file_id", "version_id", "attempt_token", "source_sha256", "source_size", "kind"}
    if (not isinstance(value, dict) or set(value) != fields
            or any(not _hex(value[key], 32) for key in ("id", "file_id", "version_id", "attempt_token"))
            or not _hex(value["source_sha256"], 64)
            or type(value["source_size"]) is not int or not 0 <= value["source_size"] <= 2**64-1
            or not isinstance(value["kind"], str) or value["kind"] not in ("", "txt", "md", "pdf")):
        raise WorkerError("invalid_claim")
    return value


class Client:
    def __init__(self, port, token):
        if type(port) is not int or not 1 <= port <= 65535 or not _hex(token, 64):
            raise WorkerError("worker_config_invalid")
        self.port, self.token = port, token

    def _request(self, method, suffix, payload=None, attempt=None, *, binary=False, replay=False):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if body is not None and len(body) > MAX_RESULT:
            raise WorkerError("result_too_large")
        headers = {"Authorization": "Bearer " + self.token, "Connection": "close", "Content-Length": str(len(body or b""))}
        if body is not None: headers["Content-Type"] = "application/json"
        if attempt is not None: headers["X-SmartDocs-Attempt"] = attempt
        for number in range(2 if replay else 1):
            # Direct HTTPConnection ignores proxy environment and cannot follow redirects.
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            try:
                connection.request(method, PREFIX + suffix, body=body, headers=headers)
                response = connection.getresponse()
                limit = MAX_INPUT if binary and response.status == 200 else 65536
                raw = response.read(limit + 1)
                if len(raw) > limit: raise WorkerError("worker_response_too_large")
                # Bounded HTTPResponse.read does not itself reject premature EOF.
                if response.length is not None and response.length > 0:
                    raise http.client.IncompleteRead(raw, response.length)
                if response.status != 200:
                    if replay and number == 0 and 500 <= response.status < 600: continue
                    try: code = json.loads(raw).get("error", {}).get("code")
                    except (ValueError, AttributeError, RecursionError): code = None
                    raise RemoteError(response.status, code if isinstance(code, str) else "worker_http_error")
                if binary: return raw
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict) or "data" not in value: raise ValueError()
                    return value["data"]
                except (ValueError, UnicodeError, RecursionError):
                    raise WorkerError("worker_response_invalid") from None
            except (OSError, http.client.HTTPException):
                if not replay or number: raise WorkerError("worker_transport_failed") from None
            finally:
                connection.close()

    def claim(self): return self._request("POST", "/claim")
    def recover(self): return self._request("POST", "/recover")
    def content(self, job):
        return self._request("GET", "/" + job["id"] + "/content", attempt=job["attempt_token"], binary=True)
    def publish(self, job, result):
        return self._request("POST", "/" + job["id"] + "/result", result, job["attempt_token"], replay=True)
    def fail(self, job, code):
        return self._request("POST", "/" + job["id"] + "/fail", {"error_code": code}, job["attempt_token"], replay=True)


def _fail(client, job, code):
    try: client.fail(job, code)
    except RemoteError as error:
        if error.status in (404, 409): return "discarded"
        raise
    return "failed"


def run_once(client):
    job = _claim(client.claim())
    if job is None: return "idle"
    try:
        if not job["kind"]: raise ParseError("unsupported_format")
        if job["source_size"] > MAX_INPUT: raise ParseError("input_too_large")
        source = client.content(job)
        if len(source) != job["source_size"] or hashlib.sha256(source).hexdigest() != job["source_sha256"]:
            raise ParseError("source_hash_mismatch")
        result = {key: job[key] for key in ("file_id", "version_id", "source_sha256")}
        if job["kind"] == "pdf":
            from .pdf_parser import parse_pdf
            parsed = parse_pdf(source, file_id=job["file_id"], version_id=job["version_id"])
            chunks = parsed.chunks
            result.update(page_count=parsed.page_count, pages_without_text=list(parsed.pages_without_text))
        else:
            chunks = parse_text(source, file_id=job["file_id"], version_id=job["version_id"], kind=job["kind"])
            result.update(page_count=0, pages_without_text=[])
        result.update(parser_revision=chunks[0].parser_revision, chunks=[asdict(chunk) for chunk in chunks])
        client.publish(job, result)
        return "completed"
    except ParseError as error:
        return _fail(client, job, error.code)
    except RemoteError as error:
        code = str(error)
        if code == "attempt_conflict": return "discarded"
        if code in SAFE_REMOTE_CODES:
            # A stale version may still own the sole running slot: finish only that attempt.
            return _fail(client, job, "worker_failed" if code == "invalid_result" else code)
        raise


@contextmanager
def worker_lock(path):
    if not os.path.isabs(path) or path == "/": raise WorkerError("worker_lock_invalid")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise WorkerError("worker_lock_invalid")
        try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise WorkerError("worker_already_running") from None
        yield
    except OSError:
        raise WorkerError("worker_lock_unavailable") from None
    finally:
        if descriptor is not None: os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Trusted single local document worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() == 0: raise WorkerError("worker_must_be_unprivileged")
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        bound = min([512*1024*1024] + [value for value in (soft, hard) if value != resource.RLIM_INFINITY])
        resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
        client = Client(int(os.environ.get("SMARTDOCS_WORKER_PORT", "1316")), os.environ.get("SMARTDOCS_WORKER_TOKEN", ""))
        with worker_lock(os.environ.get("SMARTDOCS_WORKER_LOCK_PATH", "/run/smartdocs-worker/worker.lock")):
            recovered = client.recover()
            if not isinstance(recovered, dict) or type(recovered.get("interrupted_count")) is not int:
                raise WorkerError("worker_response_invalid")
            if recovered["interrupted_count"]: print("worker: interrupted_previous_attempts", flush=True)
            while True:
                state = run_once(client)
                if state != "idle": print("worker: " + state, flush=True)
                if args.once: return 0
                if state == "idle": time.sleep(5)
    except WorkerError as error:
        print("worker: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("worker: internal_error", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
