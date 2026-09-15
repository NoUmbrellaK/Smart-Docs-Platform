#!/usr/bin/env python3
"""Real-socket acceptance scenarios for the M1 file business slice."""

import argparse
import hashlib
import http.client
import http.cookiejar
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from http.cookies import SimpleCookie


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class HttpClient:
    def __init__(self, port):
        self.port = port
        self.cookies = http.cookiejar.CookieJar()

    def _cookie_header(self):
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookies)

    def _save_cookies(self, headers):
        for raw in headers:
            parsed = SimpleCookie()
            parsed.load(raw)
            for morsel in parsed.values():
                cookie = http.cookiejar.Cookie(
                    0, morsel.key, morsel.value, None, False, "127.0.0.1",
                    False, False, morsel["path"] or "/", True, False, None,
                    True, None, None, {"HttpOnly": morsel["httponly"]}, False)
                self.cookies.set_cookie(cookie)

    def request(self, method, path, body=None, headers=None):
        request_headers = dict(headers or {})
        cookie = self._cookie_header()
        if cookie:
            request_headers["Cookie"] = cookie
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            request_headers.setdefault("Origin", f"http://127.0.0.1:{self.port}")
        if isinstance(body, (dict, list)):
            body = json.dumps(body, separators=(",", ":")).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            raw = connection.getresponse()
            header_pairs = raw.getheaders()
            response = Response(raw.status,
                                {name.lower(): value for name, value in header_pairs},
                                raw.read())
            self._save_cookies([value for name, value in header_pairs
                                if name.lower() == "set-cookie"])
            return response
        finally:
            connection.close()


class ServerProcess:
    def __init__(self, binary, log_dir):
        self.binary = str(pathlib.Path(binary).resolve())
        self.log_dir = pathlib.Path(log_dir)
        self.process = None
        self.log_handle = None
        self.port = None
        self.sequence = 0
        self.log_path = None

    @staticmethod
    def _free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def start(self, fault_point=None):
        if self.process is not None:
            raise RuntimeError("server process is already running")
        self.port = self._free_port()
        self.sequence += 1
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / f"server-{self.sequence:03d}.log"
        self.log_handle = self.log_path.open("wb")
        environment = os.environ.copy()
        environment["SMARTDOCS_LISTEN_ADDRESS"] = "127.0.0.1"
        environment["SMARTDOCS_PORT"] = str(self.port)
        if fault_point is None:
            environment.pop("SMARTDOCS_FAULT_POINT", None)
        else:
            environment["SMARTDOCS_FAULT_POINT"] = fault_point
        self.process = subprocess.Popen(
            [self.binary], cwd=str(pathlib.Path(self.binary).parents[1]),
            env=environment, stdout=self.log_handle, stderr=subprocess.STDOUT)
        for _ in range(200):
            if self.process.poll() is not None:
                code = self.process.returncode
                self._close_log()
                self.process = None
                detail = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
                raise RuntimeError(
                    f"test server exited during startup with {code}: {detail[-500:]}")
            try:
                response = HttpClient(self.port).request("GET", "/api/v1/health/live")
                if response.status == 200:
                    return self.port
            except (ConnectionError, OSError, http.client.HTTPException):
                pass
            time.sleep(0.05)
        self.stop()
        raise RuntimeError("test server did not become ready")

    def wait_for_fault_exit(self):
        if self.process is None:
            raise RuntimeError("server process is not running")
        code = self.process.wait(timeout=10)
        self.process = None
        self._close_log()
        return code

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        self._close_log()

    def _close_log(self):
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None


class Recorder:
    def __init__(self):
        self.scenarios = []
        self.current = None

    def run(self, scenario_id, name, action, deferred=None):
        record = {"id": scenario_id, "name": name, "assertions": 0,
                  "status": "fail"}
        if deferred:
            record["deferred"] = deferred
        self.scenarios.append(record)
        self.current = record
        try:
            action()
            record["status"] = "pass"
        except Exception as error:
            record["failure"] = str(error)
            raise
        finally:
            self.current = None

    def check(self, condition, message):
        if self.current is None:
            raise RuntimeError("assertion is outside a scenario")
        self.current["assertions"] += 1
        if not condition:
            raise AssertionError(f"{self.current['id']}: {message}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def data(response, status=200):
    if response.status != status:
        try:
            detail = response.json()
        except (ValueError, UnicodeDecodeError):
            detail = {"body_bytes": len(response.body)}
        raise AssertionError(f"expected HTTP {status}, got {response.status}: {detail}")
    return response.json()["data"]


def error_code(response):
    return response.json()["error"]["code"]


def login(client, username, password):
    return data(client.request("POST", "/api/v1/auth/login",
                               {"username": username, "password": password}))


def create_upload(client, project_id, directory_id, name, content, media_type,
                  file_id=None, observed_version=None):
    body = {"mode": "create_file", "directory_id": directory_id, "name": name,
            "size": len(content), "sha256": digest(content),
            "media_type": media_type}
    if file_id is not None:
        body = {"mode": "create_version", "file_id": file_id,
                "observed_current_version_id": observed_version,
                "size": len(content), "sha256": digest(content),
                "media_type": media_type}
    return data(client.request("POST", f"/api/v1/projects/{project_id}/uploads",
                               body), 201)


def put_part(client, project_id, task_id, number, content):
    return client.request(
        "PUT", f"/api/v1/projects/{project_id}/uploads/{task_id}/parts/{number}",
        content, {"Content-Type": "application/octet-stream",
                  "X-Chunk-SHA256": digest(content)})


def upload_parts(client, project_id, task, content, start=0):
    size = task["chunk_size"]
    results = []
    for number in range(start, task["part_count"]):
        chunk = content[number * size:(number + 1) * size]
        results.append(data(put_part(client, project_id, task["task_id"],
                                     number, chunk)))
    return results


def complete(client, project_id, task_id):
    return client.request(
        "POST", f"/api/v1/projects/{project_id}/uploads/{task_id}/complete")


def upload_file(client, project_id, directory_id, name, content, media_type):
    task = create_upload(client, project_id, directory_id, name, content, media_type)
    upload_parts(client, project_id, task, content)
    return task, data(complete(client, project_id, task["task_id"]))


def create_user(admin_binary, username, password):
    completed = subprocess.run(
        [admin_binary, "create-user", "--username", username, "--password-stdin"],
        input=password + "\n", text=True, capture_output=True, check=False)
    if completed.returncode != 0 or not completed.stdout.startswith("created_user_id="):
        raise RuntimeError(f"create-user failed for {username}: exit {completed.returncode}")
    return completed.stdout.strip().split("=", 1)[1]


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-bin", required=True)
    parser.add_argument("--admin-bin", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--plain", required=True)
    parser.add_argument("--pdf", required=True)
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    password = os.environ.get("SMARTDOCS_E2E_PASSWORD")
    if not password:
        raise RuntimeError("SMARTDOCS_E2E_PASSWORD is required")

    users = {name: create_user(arguments.admin_bin, f"m1-{name}", password)
             for name in ("admin", "editor", "reader")}
    server = ServerProcess(arguments.server_bin, arguments.log_dir)
    recorder = Recorder()
    plain = pathlib.Path(arguments.plain).read_bytes()
    pdf = pathlib.Path(arguments.pdf).read_bytes()
    state = {}

    try:
        server.start()
        admin = HttpClient(server.port)
        editor = HttpClient(server.port)
        reader = HttpClient(server.port)
        anonymous = HttpClient(server.port)
        login(admin, "m1-admin", password)
        login(editor, "m1-editor", password)
        login(reader, "m1-reader", password)
        project_a = data(admin.request("POST", "/api/v1/projects", {"name": "M1 Alpha"}), 201)
        project_b = data(editor.request("POST", "/api/v1/projects", {"name": "M1 Beta"}), 201)
        a_id, a_root = project_a["id"], project_a["root_directory_id"]
        b_id, b_root = project_b["id"], project_b["root_directory_id"]
        data(admin.request("PUT", f"/api/v1/projects/{a_id}/members/{users['editor']}",
                           {"role": "editor"}))
        _, a_initial = upload_file(admin, a_id, a_root, "alpha-seed.txt", plain,
                                   "text/plain")
        _, b_initial = upload_file(editor, b_id, b_root, "beta-seed.txt", plain,
                                   "text/plain")

        def t01():
            checks = [
                (reader.request("GET", f"/api/v1/projects/{a_id}/files"), 404),
                (admin.request("GET", f"/api/v1/projects/{b_id}/files"), 404),
                (admin.request("GET", f"/api/v1/projects/{b_id}/files/{b_initial['file_id']}/content"), 404),
                (admin.request("GET", f"/api/v1/projects/{b_id}/files/{b_initial['file_id']}/content",
                               headers={"Range": "bytes=0-3"}), 404),
                (admin.request("GET", f"/api/v1/projects/{a_id}/files/{b_initial['file_id']}/content"), 404),
                (anonymous.request("GET", f"/api/v1/projects/{a_id}/files/{a_initial['file_id']}/content"), 401),
                (admin.request("GET", f"/api/v1/projects/{b_id}/files/{b_initial['file_id']}"
                               f"/versions/{b_initial['version_id']}/content"), 404),
                (admin.request("GET", f"/api/v1/projects/{a_id}/files/{b_initial['file_id']}"
                               f"/versions/{b_initial['version_id']}/content"), 404),
                (anonymous.request("GET", f"/api/v1/projects/{a_id}/files/{a_initial['file_id']}"
                                   f"/versions/{a_initial['version_id']}/content"), 401),
            ]
            for response, expected in checks:
                recorder.check(response.status == expected,
                               f"ID/role substitution expected {expected}, got {response.status}")
            recorder.check(editor.request("GET", f"/api/v1/projects/{a_id}/files").status == 200,
                           "editor membership did not permit listing")

        recorder.run("T-01", "ID substitution and role/project authorization", t01)

        def t02():
            content = b"0123456789abcdefFEDCBA9876543210"
            task = create_upload(admin, a_id, a_root, "chunk-replay.bin", content,
                                 "application/octet-stream")
            chunk = content[:task["chunk_size"]]
            first = data(put_part(admin, a_id, task["task_id"], 0, chunk))
            replay = data(put_part(admin, a_id, task["task_id"], 0, chunk))
            different = b"x" * len(chunk)
            conflict = put_part(admin, a_id, task["task_id"], 0, different)
            recorder.check(first["reused"] is False, "first part was marked reused")
            recorder.check(replay["reused"] is True, "identical replay was not reused")
            recorder.check(conflict.status == 409 and error_code(conflict) == "chunk_conflict",
                           "different part bytes did not conflict")
            upload_parts(admin, a_id, task, content, 1)
            recorder.check(complete(admin, a_id, task["task_id"]).status == 200,
                           "chunk replay task could not complete")

        recorder.run("T-02", "Chunk replay idempotence and conflict", t02)

        def t03():
            task = create_upload(admin, a_id, a_root, "restart-resume.txt", plain,
                                 "text/plain")
            first_chunk = plain[:task["chunk_size"]]
            data(put_part(admin, a_id, task["task_id"], 0, first_chunk))
            before = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/uploads/{task['task_id']}"))
            server.stop()
            server.start()
            for client in (admin, editor, reader, anonymous):
                client.port = server.port
            after = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/uploads/{task['task_id']}"))
            recorder.check([part["part_number"] for part in before["confirmed_parts"]] == [0],
                           "first part was not confirmed before restart")
            recorder.check([part["part_number"] for part in after["confirmed_parts"]] == [0],
                           "confirmed part did not survive restart")
            recorder.check(after["state"] == "interrupted",
                           "restart did not expose truthful interrupted state")
            upload_parts(admin, a_id, task, plain, 1)
            first = data(complete(admin, a_id, task["task_id"]))
            downloaded = admin.request(
                "GET", f"/api/v1/projects/{a_id}/files/{first['file_id']}/content")
            recorder.check(downloaded.status == 200 and digest(downloaded.body) == digest(plain),
                           "resumed upload hash differs")
            state.update({"resume_task": task, "resume_result": first})

        recorder.run("T-03", "Restart resume preserves confirmed parts and hash", t03)

        def t04():
            task = state["resume_task"]
            first = state["resume_result"]
            replay = data(complete(admin, a_id, task["task_id"]))
            recorder.check(first["file_id"] == replay["file_id"] and
                           first["version_id"] == replay["version_id"] and
                           first["processing_job_id"] == replay["processing_job_id"],
                           "completion replay changed result IDs")
            recorder.check(replay["reused"] is True,
                           "completion replay was not marked reused")
            partial = create_upload(admin, a_id, a_root, "hidden-partial.txt", plain,
                                    "text/plain")
            hidden = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/files?name=hidden-partial.txt"))
            detail = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/uploads/{partial['task_id']}"))
            recorder.check(hidden["total"] == 0 and detail["file_id"] is None,
                           "incomplete upload became visible")

        recorder.run("T-04", "Idempotent complete and hidden partials", t04)

        def t05():
            _, result = upload_file(admin, a_id, a_root, "preview.pdf", pdf,
                                    "application/pdf")
            path = (f"/api/v1/projects/{a_id}/files/{result['file_id']}/versions/"
                    f"{result['version_id']}/content")
            full = admin.request("GET", path)
            recorder.check(full.status == 200 and full.body == pdf,
                           "full PDF response differs")
            recorder.check(full.headers.get("content-type") == "application/pdf" and
                           full.headers.get("content-disposition", "").startswith("inline;"),
                           "PDF headers do not permit inline preview")
            ranges = [("bytes=4-", pdf[4:], f"bytes 4-{len(pdf)-1}/{len(pdf)}"),
                      ("bytes=2-7", pdf[2:8], f"bytes 2-7/{len(pdf)}"),
                      ("bytes=-5", pdf[-5:], f"bytes {len(pdf)-5}-{len(pdf)-1}/{len(pdf)}")]
            for header, expected, content_range in ranges:
                response = admin.request("GET", path, headers={"Range": header})
                recorder.check(response.status == 206 and response.body == expected and
                               response.headers.get("content-range") == content_range,
                               f"Range {header} was not served exactly")
            invalid = admin.request("GET", path, headers={"Range": f"bytes={len(pdf)}-"})
            recorder.check(invalid.status == 416 and
                           invalid.headers.get("content-range") == f"bytes */{len(pdf)}",
                           "invalid Range did not return 416 boundary")
            malformed = admin.request("GET", path, headers={"Range": "bytes=bogus"})
            recorder.check(malformed.status == 416 and
                           malformed.headers.get("content-range") == f"bytes */{len(pdf)}",
                           "malformed Range did not return 416 boundary")
            multi = admin.request("GET", path, headers={"Range": "bytes=0-1,4-5"})
            recorder.check(multi.status == 501 and error_code(multi) == "range_not_supported",
                           "multi Range did not return explicit 501")
            preview_url = path + "#page=1"
            parsed = urllib.parse.urlsplit(preview_url)
            preview = admin.request("GET", parsed.path)
            recorder.check(parsed.fragment == "page=1" and preview.status == 200 and
                           preview.body == pdf,
                           "client-side PDF page fragment contract failed")

        recorder.run("T-05", "Full/single Range and PDF fragment behavior", t05)

        def t06():
            old_result = state["resume_result"]
            file_id, old_version = old_result["file_id"], old_result["version_id"]
            stale_content = b"stale version must never become current\n"
            stale = create_upload(admin, a_id, None, None, stale_content, "text/plain",
                                  file_id, old_version)
            new_content = b"new current version from M1\n"
            current = create_upload(admin, a_id, None, None, new_content, "text/plain",
                                    file_id, old_version)
            upload_parts(admin, a_id, current, new_content)
            new_result = data(complete(admin, a_id, current["task_id"]))
            old_path = (f"/api/v1/projects/{a_id}/files/{file_id}/versions/"
                        f"{old_version}/content")
            current_path = f"/api/v1/projects/{a_id}/files/{file_id}/content"
            recorder.check(admin.request("GET", old_path).body == plain,
                           "old version URL changed content")
            recorder.check(admin.request("GET", current_path).body == new_content,
                           "current URL did not resolve the new version")
            state.update({"file_id": file_id, "old_version": old_version,
                          "new_version": new_result["version_id"],
                          "new_content": new_content, "stale_task": stale,
                          "stale_content": stale_content})

        recorder.run("T-06", "Historical and current version URLs remain fixed", t06)

        def t07():
            file_id = state["file_id"]
            current_path = f"/api/v1/projects/{a_id}/files/{file_id}/content"
            stale = state["stale_task"]
            stale_content = state["stale_content"]
            upload_parts(admin, a_id, stale, stale_content)
            conflict = complete(admin, a_id, stale["task_id"])
            recorder.check(conflict.status == 409 and error_code(conflict) == "version_conflict",
                           "stale version task replaced a newer current version")
            recorder.check(admin.request("GET", current_path).body == state["new_content"],
                           "stale completion altered current content")

        recorder.run("T-07", "Stale upload cannot replace current", t07,
                     ["stale parsing/index publication is M2"])

        def t08():
            file_id = state["file_id"]
            directory = data(admin.request(
                "POST", f"/api/v1/projects/{a_id}/directories",
                {"parent_id": a_root, "name": "Moved"}), 201)
            moved = data(admin.request(
                "PATCH", f"/api/v1/projects/{a_id}/files/{file_id}",
                {"name": "moved.txt", "directory_id": directory["id"]}))
            recorder.check(moved["id"] == file_id and
                           moved["current_version_id"] == state["new_version"],
                           "move changed file or current-version ID")
            root_list = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/files?directory_id={a_root}"))
            moved_list = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/files?directory_id={directory['id']}"))
            recorder.check(all(item["id"] != file_id for item in root_list["items"]) and
                           any(item["id"] == file_id for item in moved_list["items"]),
                           "move was not immediately reflected in lists")
            deleted = admin.request("DELETE", f"/api/v1/projects/{a_id}/files/{file_id}")
            hidden = data(admin.request("GET", f"/api/v1/projects/{a_id}/files?name=moved.txt"))
            denied = admin.request("GET", f"/api/v1/projects/{a_id}/files/{file_id}/content")
            deleted_list = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/files?name=moved.txt&deleted=true"))
            recorder.check(deleted.status == 204 and hidden["total"] == 0 and
                           denied.status == 404 and deleted_list["total"] == 1,
                           "delete did not immediately affect list/download")
            restored = data(admin.request(
                "POST", f"/api/v1/projects/{a_id}/files/{file_id}/restore"))
            recorder.check(restored["id"] == file_id and
                           admin.request("GET", f"/api/v1/projects/{a_id}/files/{file_id}/content").status == 200,
                           "restore did not immediately restore download")
            before = reader.request("GET", f"/api/v1/projects/{a_id}/files")
            data(admin.request("PUT", f"/api/v1/projects/{a_id}/members/{users['reader']}",
                               {"role": "reader"}))
            after_list = reader.request("GET", f"/api/v1/projects/{a_id}/files")
            after_download = reader.request(
                "GET", f"/api/v1/projects/{a_id}/files/{file_id}/content")
            recorder.check(before.status == 404 and after_list.status == 200 and
                           after_download.status == 200,
                           "permission addition was not immediately reflected")
            server.stop()
            server.start()
            for client in (admin, editor, reader, anonymous):
                client.port = server.port
            recorder.check(admin.request("GET", "/api/v1/health/ready").status == 200,
                           "server could not reconcile a legitimately moved file")

        recorder.run("T-08", "Move/delete/restore/permission changes are immediate", t08,
                     ["index/report effects are M2/M4"])

        def t09():
            versions = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/files/{state['file_id']}/versions"))["items"]
            expected = {state["old_version"], state["new_version"]}
            visible = {version["id"] for version in versions}
            recorder.check(expected.issubset(visible), "version processing states were not visible")
            recorder.check(all(version["processing_state"] in {"pending", "processing", "failed"}
                               for version in versions if version["id"] in expected),
                           "M1 falsely claimed parsing/indexing completion")
            task = data(admin.request(
                "GET", f"/api/v1/projects/{a_id}/uploads/{state['resume_task']['task_id']}"))
            recorder.check(task["state"] == "completed" and task["processing_job_id"],
                           "upload and processing job status were not visible")

        recorder.run("T-09", "Truthful visible M1 processing status", t09,
                     ["parsing, metadata retrieval, and vector fallback are M2"])

        pathlib.Path(arguments.context_file).write_text(
            json.dumps({"project_id": a_id, "root_directory_id": a_root,
                        "username": "m1-admin"}), encoding="utf-8")
    finally:
        server.stop()

    evidence = {
        "schema_version": 1,
        "transport": "loopback_tcp",
        "scenarios": recorder.scenarios,
        "passed": sum(item["status"] == "pass" for item in recorder.scenarios),
        "failed": sum(item["status"] != "pass" for item in recorder.scenarios),
        "assertions": sum(item["assertions"] for item in recorder.scenarios),
        "fixture_sha256": {"plain.txt": digest(plain), "sample.pdf": digest(pdf)},
    }
    pathlib.Path(arguments.evidence_file).write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"M1 HTTP: {evidence['passed']}/{len(evidence['scenarios'])} scenarios, "
          f"{evidence['assertions']} assertions")
    return 0 if evidence["failed"] == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"m1_http_failed: {error}", file=sys.stderr)
        sys.exit(1)
