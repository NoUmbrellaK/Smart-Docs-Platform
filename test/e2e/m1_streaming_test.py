#!/usr/bin/env python3
"""Measured real-socket streaming proof for F-02.9."""

import argparse
import concurrent.futures
import contextlib
import hashlib
import http.client
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time

from m1_http_test import HttpClient, Response, ServerProcess, data


FILE_SIZE = 32 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
CONCURRENCY = 4
RSS_GROWTH_LIMIT = 64 * 1024 * 1024
IO_BLOCK_SIZE = 64 * 1024
PHASES = [
    "concurrent_streamed_part_upload",
    "concurrent_upload_completion_integrity_verification",
    "concurrent_streamed_download",
]


def parse_proc_status(content):
    fields = re.findall(r"^(VmRSS|VmHWM):[ \t]+([0-9]+) kB$", content,
                        re.MULTILINE)
    if len(fields) != 2 or {name for name, _ in fields} != {"VmRSS", "VmHWM"}:
        raise ValueError("malformed /proc status: VmRSS/VmHWM are required in kB")
    values = {name: int(value) * 1024 for name, value in fields}
    return {"vm_rss_bytes": values["VmRSS"],
            "vm_hwm_bytes": values["VmHWM"]}


def read_proc_status(pid):
    try:
        content = pathlib.Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError) as error:
        raise RuntimeError("measurement server exited before RSS collection") from error
    return parse_proc_status(content)


def peak_growth(baseline_rss, final_hwm, limit):
    if baseline_rss < 0 or final_hwm < baseline_rss or limit <= 0:
        raise ValueError("invalid RSS measurement")
    growth = final_hwm - baseline_rss
    return {"peak_growth_bytes": growth, "within_limit": growth < limit}


class Checker:
    def __init__(self):
        self.assertions = 0
        self.lock = threading.Lock()

    def require(self, condition, message):
        with self.lock:
            self.assertions += 1
        if not condition:
            raise AssertionError(message)


class PhaseConcurrency:
    def __init__(self, name, configured_concurrency):
        self.name = name
        self.configured_concurrency = configured_concurrency
        self.start_barrier = threading.Barrier(configured_concurrency)
        self.lock = threading.Lock()
        self.intervals = []

    @contextlib.contextmanager
    def request(self, synchronize=False):
        if synchronize:
            self.start_barrier.wait(timeout=120)
        started = time.monotonic_ns()
        try:
            yield
        finally:
            ended = time.monotonic_ns()
            with self.lock:
                self.intervals.append((started, ended))

    def result(self, expected_requests):
        events = []
        for started, ended in self.intervals:
            events.extend(((started, 1), (ended, -1)))
        active = 0
        observed = 0
        for _, change in sorted(events, key=lambda event: (event[0], event[1])):
            active += change
            observed = max(observed, active)
        if len(self.intervals) != expected_requests:
            raise AssertionError(
                f"{self.name} recorded {len(self.intervals)} requests, "
                f"expected {expected_requests}")
        if observed != self.configured_concurrency:
            raise AssertionError(
                f"{self.name} did not reach configured concurrency "
                f"{self.configured_concurrency}; observed {observed}")
        return {
            "name": self.name,
            "configured_concurrency": self.configured_concurrency,
            "observed_max_concurrency": observed,
            "request_count": len(self.intervals),
        }


class BoundedReader:
    def __init__(self, source, length):
        self.source = source
        self.remaining = length

    def read(self, size=-1):
        if self.remaining == 0:
            return b""
        if size < 0 or size > self.remaining:
            size = self.remaining
        chunk = self.source.read(size)
        if not chunk:
            raise EOFError("source file ended during streamed upload")
        self.remaining -= len(chunk)
        return chunk


def request(client, method, path, body=None, headers=None, stream_response=False):
    request_headers = dict(headers or {})
    cookie = client._cookie_header()
    if cookie:
        request_headers["Cookie"] = cookie
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        request_headers.setdefault("Origin", f"http://127.0.0.1:{client.port}")
    if isinstance(body, (dict, list)):
        body = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=120)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        raw = connection.getresponse()
    except BaseException:
        connection.close()
        raise
    if stream_response:
        return connection, raw
    try:
        pairs = raw.getheaders()
        response = Response(raw.status,
                            {name.lower(): value for name, value in pairs},
                            raw.read())
        client._save_cookies([value for name, value in pairs
                              if name.lower() == "set-cookie"])
        return response
    finally:
        connection.close()


def login(client, username, password):
    # Setup shares the streaming I/O budget; never retry a credential POST.
    return data(request(client, "POST", "/api/v1/auth/login",
                        {"username": username, "password": password}))


def write_fixture(path, index):
    seed = hashlib.sha256(f"smartdocs-f02.9-{index}".encode("ascii")).digest()
    block = (seed * (IO_BLOCK_SIZE // len(seed) + 1))[:IO_BLOCK_SIZE]
    whole = hashlib.sha256()
    parts = []
    part = hashlib.sha256()
    written_in_part = 0
    with path.open("wb") as output:
        remaining = FILE_SIZE
        while remaining:
            chunk = block[:min(len(block), remaining)]
            output.write(chunk)
            whole.update(chunk)
            part.update(chunk)
            remaining -= len(chunk)
            written_in_part += len(chunk)
            if written_in_part == UPLOAD_CHUNK_SIZE:
                parts.append(part.hexdigest())
                part = hashlib.sha256()
                written_in_part = 0
    if written_in_part:
        parts.append(part.hexdigest())
    return whole.hexdigest(), parts


def create_task(client, context, name, source_sha256):
    return data(request(
        client, "POST", f"/api/v1/projects/{context['project_id']}/uploads",
        {"mode": "create_file", "directory_id": context["root_directory_id"],
         "name": name, "size": FILE_SIZE, "sha256": source_sha256,
         "media_type": "application/octet-stream"}), 201)


def upload_one(client, project_id, task, fixture, part_hashes, checker,
               phase):
    with fixture.open("rb") as source:
        for number, expected_hash in enumerate(part_hashes):
            source.seek(number * UPLOAD_CHUNK_SIZE)
            with phase.request(synchronize=number == 0):
                response = request(
                    client, "PUT",
                    f"/api/v1/projects/{project_id}/uploads/{task['task_id']}"
                    f"/parts/{number}",
                    BoundedReader(source, UPLOAD_CHUNK_SIZE),
                    {"Content-Type": "application/octet-stream",
                     "Content-Length": str(UPLOAD_CHUNK_SIZE),
                     "X-Chunk-SHA256": expected_hash})
            checker.require(response.status == 200,
                            f"streamed part upload returned HTTP {response.status}")
            part = data(response)
            checker.require(part["part_number"] == number and
                            part["size"] == UPLOAD_CHUNK_SIZE and
                            part["sha256"] == expected_hash and
                            part["reused"] is False,
                            f"streamed part {number} response was inconsistent")


def complete_one(client, project_id, task, checker, phase):
    with phase.request(synchronize=True):
        response = request(
            client, "POST",
            f"/api/v1/projects/{project_id}/uploads/{task['task_id']}/complete")
    checker.require(response.status == 200,
                    f"upload completion returned HTTP {response.status}")
    result = data(response)
    checker.require(all(result.get(name) for name in
                        ("file_id", "version_id", "processing_job_id")),
                    "upload completion omitted durable result IDs")
    detail = data(request(
        client, "GET",
        f"/api/v1/projects/{project_id}/uploads/{task['task_id']}"))
    checker.require(detail["state"] == "completed" and
                    detail["file_id"] == result["file_id"] and
                    detail["version_id"] == result["version_id"] and
                    detail["processing_job_id"] == result["processing_job_id"],
                    "completed upload task did not match durable result IDs")
    return result


def download_one(client, project_id, result, expected_hash, checker, phase):
    with phase.request(synchronize=True):
        connection, response = request(
            client, "GET",
            f"/api/v1/projects/{project_id}/files/{result['file_id']}/content",
            stream_response=True)
        try:
            checker.require(response.status == 200,
                            f"streamed download returned HTTP {response.status}")
            checker.require(response.getheader("Content-Length") == str(FILE_SIZE),
                            "streamed download Content-Length was wrong")
            digest = hashlib.sha256()
            received = 0
            while True:
                chunk = response.read(IO_BLOCK_SIZE)
                if not chunk:
                    break
                received += len(chunk)
                digest.update(chunk)
            actual_hash = digest.hexdigest()
            checker.require(received == FILE_SIZE and actual_hash == expected_hash,
                            "streamed download size or SHA-256 was wrong")
            return actual_hash
        finally:
            connection.close()


def ensure_server_alive(server, checker):
    checker.require(server.process is not None and server.process.poll() is None,
                    "measurement server exited")


def run_concurrently(function, arguments):
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = [executor.submit(function, *items) for items in arguments]
        return [future.result() for future in futures]


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-bin", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    password = os.environ.get("SMARTDOCS_E2E_PASSWORD")
    if not password:
        raise RuntimeError("SMARTDOCS_E2E_PASSWORD is required")
    context = json.loads(pathlib.Path(arguments.context_file).read_text(
        encoding="utf-8"))
    checker = Checker()
    server = ServerProcess(arguments.server_bin, arguments.log_dir)
    with tempfile.TemporaryDirectory(prefix="smartdocs-f02.9.") as root:
        root = pathlib.Path(root)
        fixtures = [root / f"streaming-{index}.bin"
                    for index in range(CONCURRENCY)]
        generated = [write_fixture(path, index)
                     for index, path in enumerate(fixtures)]
        source_hashes = [item[0] for item in generated]
        part_hashes = [item[1] for item in generated]
        try:
            print("M1 streaming: fixtures ready; starting measurement server", flush=True)
            server.start(environment_overrides={
                "SMARTDOCS_MAX_FILE_BYTES": str(FILE_SIZE),
                "SMARTDOCS_CHUNK_BYTES": str(UPLOAD_CHUNK_SIZE),
            })
            clients = [HttpClient(server.port) for _ in range(CONCURRENCY)]
            print("M1 streaming: authenticating measurement clients", flush=True)
            for client in clients:
                login(client, context["username"], password)
            tasks = [create_task(client, context, fixture.name, source_hash)
                     for client, fixture, source_hash in
                     zip(clients, fixtures, source_hashes)]
            print("M1 streaming: tasks ready; measuring transfer phases", flush=True)
            for task in tasks:
                checker.require(task["chunk_size"] == UPLOAD_CHUNK_SIZE,
                                "server did not use the required 8 MiB chunks")
                checker.require(task["part_count"] == 4,
                                "32 MiB file did not produce four parts")
            ensure_server_alive(server, checker)
            baseline = read_proc_status(server.process.pid)

            upload_phase = PhaseConcurrency(PHASES[0], CONCURRENCY)
            run_concurrently(upload_one, [
                (client, context["project_id"], task, fixture, hashes, checker,
                 upload_phase)
                for client, task, fixture, hashes in
                zip(clients, tasks, fixtures, part_hashes)])
            upload_observation = upload_phase.result(expected_requests=16)
            ensure_server_alive(server, checker)

            completion_phase = PhaseConcurrency(PHASES[1], CONCURRENCY)
            results = run_concurrently(complete_one, [
                (client, context["project_id"], task, checker, completion_phase)
                for client, task in zip(clients, tasks)])
            completion_observation = completion_phase.result(expected_requests=4)
            ensure_server_alive(server, checker)

            download_phase = PhaseConcurrency(PHASES[2], CONCURRENCY)
            download_hashes = run_concurrently(download_one, [
                (client, context["project_id"], result, expected, checker,
                 download_phase)
                for client, result, expected in
                zip(clients, results, source_hashes)])
            download_observation = download_phase.result(expected_requests=4)
            ensure_server_alive(server, checker)
            final = read_proc_status(server.process.pid)
        finally:
            server.stop()

    growth = peak_growth(baseline["vm_rss_bytes"], final["vm_hwm_bytes"],
                         RSS_GROWTH_LIMIT)
    checker.require(growth["within_limit"],
                    "streaming peak RSS growth was not strictly below 64 MiB")
    evidence = {
        "schema_version": 2,
        "status": "pass",
        "file_size_bytes": FILE_SIZE,
        "upload_chunk_size_bytes": UPLOAD_CHUNK_SIZE,
        "phases": [upload_observation, completion_observation,
                   download_observation],
        "hashes": [
            {"file": f"streaming-{index}.bin",
             "source_sha256": source_hashes[index],
             "download_sha256": download_hashes[index]}
            for index in range(CONCURRENCY)
        ],
        "rss": {
            "baseline_vm_rss_bytes": baseline["vm_rss_bytes"],
            "baseline_vm_hwm_bytes": baseline["vm_hwm_bytes"],
            "final_peak_vm_hwm_bytes": final["vm_hwm_bytes"],
            "peak_growth_bytes": growth["peak_growth_bytes"],
            "growth_limit_bytes": RSS_GROWTH_LIMIT,
        },
        "assertions": checker.assertions,
    }
    output = pathlib.Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"M1 streaming: {CONCURRENCY} x {FILE_SIZE} bytes; "
          f"peak growth {growth['peak_growth_bytes']}/{RSS_GROWTH_LIMIT} bytes; "
          f"{checker.assertions} assertions")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"m1_streaming_failed: {error}", file=sys.stderr)
        sys.exit(1)
