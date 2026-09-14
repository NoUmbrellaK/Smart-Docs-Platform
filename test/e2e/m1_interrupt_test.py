#!/usr/bin/env python3
"""Twenty deterministic real-process interruption and recovery rounds."""

import argparse
import datetime
import hashlib
import http.client
import json
import os
import pathlib
import subprocess
import sys

from m1_http_test import (HttpClient, ServerProcess, complete, create_upload,
                          data, digest, login, upload_parts)


FAULT_ROUNDS = (
    [("AfterPartTempFsync", 4),
     ("AfterAssembledFsync", 4),
     ("AfterObjectRename", 4),
     ("BeforeDatabaseCommit", 3),
     ("AfterDatabaseCommit", 3),
     ("BeforeHttpResponse", 2)]
)


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-bin", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--http-evidence", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def mysql_scalar(query):
    environment = os.environ.copy()
    environment["MYSQL_PWD"] = environment["SMARTDOCS_MYSQL_PASSWORD"]
    command = ["mysql", "--batch", "--skip-column-names", "--protocol=SOCKET",
               f"--socket={environment['SMARTDOCS_MYSQL_SOCKET']}",
               f"--user={environment['SMARTDOCS_MYSQL_USER']}",
               environment["SMARTDOCS_MYSQL_DATABASE"], "--execute", query]
    completed = subprocess.run(command, env=environment, text=True,
                               capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"mysql evidence query failed with exit {completed.returncode}")
    return completed.stdout.strip()


def quote(value):
    if len(value) != 32 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("evidence query ID is invalid")
    return "'" + value + "'"


def graph_counts(file_id, version_id, job_id, task_id):
    query = (
        "SELECT "
        f"(SELECT COUNT(*) FROM files WHERE id={quote(file_id)}),"
        f"(SELECT COUNT(*) FROM file_versions WHERE file_id={quote(file_id)}),"
        f"(SELECT COUNT(*) FROM file_versions WHERE id={quote(version_id)} "
        f"AND file_id={quote(file_id)}),"
        f"(SELECT COUNT(*) FROM processing_jobs WHERE id={quote(job_id)} "
        f"AND file_version_id={quote(version_id)}),"
        f"(SELECT COUNT(*) FROM upload_tasks WHERE id={quote(task_id)} "
        f"AND result_file_id={quote(file_id)} AND result_version_id={quote(version_id)} "
        f"AND processing_job_id={quote(job_id)} AND state='completed')")
    values = [int(value) for value in mysql_scalar(query).split("\t")]
    if len(values) != 5:
        raise AssertionError("unexpected graph evidence shape")
    return {"files": values[0], "versions": values[1],
            "matching_versions": values[2], "processing_jobs": values[3],
            "completed_tasks": values[4]}


def git_metadata(repo_root):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                          text=True, capture_output=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=repo_root, text=True, capture_output=True, check=True).stdout.strip())
    return {"head": head, "dirty": dirty}


def trigger_and_wait(client, server, method, path, body=None, headers=None):
    disconnected = False
    try:
        response = client.request(method, path, body, headers)
        disconnected = response.status == 0
    except (ConnectionError, OSError, http.client.HTTPException):
        disconnected = True
    exit_code = server.wait_for_fault_exit()
    check(disconnected, "fault request unexpectedly received a complete response")
    check(exit_code == 86, f"faulted server exited {exit_code}, expected 86")
    return exit_code


def run_round(server, client, project_id, root_directory_id, point, number):
    name = f"interrupt-{number:02d}-{point}.txt"
    content = f"M1 interruption round {number:02d} at {point}\n".encode("ascii")
    expected_sha256 = digest(content)
    assertions = 0

    def require(condition, message):
        nonlocal assertions
        assertions += 1
        check(condition, message)

    server.start()
    client.port = server.port
    task = create_upload(client, project_id, root_directory_id, name, content,
                         "text/plain")
    if point != "AfterPartTempFsync":
        upload_parts(client, project_id, task, content)
    server.stop()

    server.start(point)
    client.port = server.port
    if point == "AfterPartTempFsync":
        first = content[:task["chunk_size"]]
        exit_code = trigger_and_wait(
            client, server, "PUT",
            f"/api/v1/projects/{project_id}/uploads/{task['task_id']}/parts/0",
            first, {"Content-Type": "application/octet-stream",
                    "X-Chunk-SHA256": digest(first)})
    else:
        exit_code = trigger_and_wait(
            client, server, "POST",
            f"/api/v1/projects/{project_id}/uploads/{task['task_id']}/complete")
    assertions += 2

    server.start()
    client.port = server.port
    restarted = data(client.request(
        "GET", f"/api/v1/projects/{project_id}/uploads/{task['task_id']}"))
    confirmed_after_restart = [part["part_number"]
                               for part in restarted["confirmed_parts"]]
    committed_fault = point in {"AfterDatabaseCommit", "BeforeHttpResponse"}
    expected_state = "completed" if committed_fault else "interrupted"
    require(restarted["state"] == expected_state,
            f"{point} restart state {restarted['state']} != {expected_state}")
    expected_confirmed = [] if point == "AfterPartTempFsync" else list(range(task["part_count"]))
    require(confirmed_after_restart == expected_confirmed,
            f"{point} confirmed parts changed across restart")

    listed = data(client.request(
        "GET", f"/api/v1/projects/{project_id}/files?name={urllib_quote(name)}"))
    if committed_fault:
        require(listed["total"] == 1, "committed publication was not visible")
        published_id = restarted["file_id"]
        visible = client.request(
            "GET", f"/api/v1/projects/{project_id}/files/{published_id}/content")
        require(visible.status == 200 and digest(visible.body) == expected_sha256,
                "committed publication was not a complete downloadable object")
        half_published = False
    else:
        require(listed["total"] == 0, "uncommitted publication became list-visible")
        candidate = client.request(
            "GET", f"/api/v1/projects/{project_id}/files/{task['task_id']}/content")
        require(candidate.status == 404,
                "uncommitted task-named object became downloadable")
        require(int(mysql_scalar(
            f"SELECT COUNT(*) FROM files WHERE name='{name}'")) == 0,
            "uncommitted publication created file metadata")
        half_published = False

    if point == "AfterPartTempFsync":
        upload_parts(client, project_id, task, content)
    result = data(complete(client, project_id, task["task_id"]))
    final_task = data(client.request(
        "GET", f"/api/v1/projects/{project_id}/uploads/{task['task_id']}"))
    require(final_task["state"] == "completed", "retry did not complete the task")
    require([part["part_number"] for part in final_task["confirmed_parts"]] ==
            list(range(task["part_count"])), "final confirmed parts are incomplete")
    require(result["file_id"] == final_task["file_id"] and
            result["version_id"] == final_task["version_id"] and
            result["processing_job_id"] == final_task["processing_job_id"],
            "result IDs differ from durable task state")
    if committed_fault:
        require(result["reused"] is True, "committed response-loss retry was not reused")

    downloaded = client.request(
        "GET", f"/api/v1/projects/{project_id}/files/{result['file_id']}/content")
    final_sha256 = hashlib.sha256(downloaded.body).hexdigest()
    require(downloaded.status == 200 and final_sha256 == expected_sha256,
            "final content hash differs after retry")
    counts = graph_counts(result["file_id"], result["version_id"],
                          result["processing_job_id"], task["task_id"])
    duplicate_version_count = max(0, counts["versions"] - 1)
    require(counts == {"files": 1, "versions": 1, "matching_versions": 1,
                       "processing_jobs": 1, "completed_tasks": 1},
            f"published graph is not singleton: {counts}")
    require(duplicate_version_count == 0, "duplicate file version was published")
    server.stop()

    return {
        "round": number,
        "fault_point": point,
        "process_exit": exit_code,
        "restart": "ready",
        "task_state_after_restart": restarted["state"],
        "confirmed_parts_after_restart": confirmed_after_restart,
        "result_ids": {"file_id": result["file_id"],
                       "version_id": result["version_id"],
                       "processing_job_id": result["processing_job_id"]},
        "final_sha256": final_sha256,
        "duplicate_version_count": duplicate_version_count,
        "singleton_graph": counts,
        "half_published_download": half_published,
        "assertions": assertions,
        "status": "pass",
    }


def urllib_quote(value):
    # The fixture names are ASCII, but use the URL encoder to keep the boundary honest.
    import urllib.parse
    return urllib.parse.quote(value, safe="")


def main():
    arguments = parse_arguments()
    password = os.environ.get("SMARTDOCS_E2E_PASSWORD")
    if not password:
        raise RuntimeError("SMARTDOCS_E2E_PASSWORD is required")
    context = json.loads(pathlib.Path(arguments.context_file).read_text(encoding="utf-8"))
    http_evidence = json.loads(
        pathlib.Path(arguments.http_evidence).read_text(encoding="utf-8"))
    server = ServerProcess(arguments.server_bin, arguments.log_dir)
    client = HttpClient(0)
    rounds = []
    round_number = 0
    try:
        server.start()
        client.port = server.port
        login(client, context["username"], password)
        server.stop()
        for point, count in FAULT_ROUNDS:
            for _ in range(count):
                round_number += 1
                rounds.append(run_round(
                    server, client, context["project_id"],
                    context["root_directory_id"], point, round_number))
                print(f"M1 interruption {round_number:02d}/20 {point}: pass")
    finally:
        server.stop()

    distribution = {point: sum(item["fault_point"] == point for item in rounds)
                    for point, _ in FAULT_ROUNDS}
    expected_distribution = dict(FAULT_ROUNDS)
    check(distribution == expected_distribution,
          f"fault distribution {distribution} != {expected_distribution}")
    check(len(rounds) == 20 and all(item["status"] == "pass" for item in rounds),
          "not all twenty interruption rounds passed")

    repo_root = str(pathlib.Path(__file__).resolve().parents[2])
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    result = {
        "schema_version": 1,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "commit": git_metadata(repo_root),
        "environment": {"http_transport": "loopback_tcp",
                        "mysql_transport": "private_unix_socket",
                        "server": "smartdocs_test_server",
                        "python_dependencies": "standard_library_only"},
        "summary": {
            "http_scenarios_total": len(http_evidence["scenarios"]),
            "http_scenarios_passed": http_evidence["passed"],
            "http_scenarios_failed": http_evidence["failed"],
            "http_assertions": http_evidence["assertions"],
            "interruption_rounds_total": len(rounds),
            "interruption_rounds_passed": sum(item["status"] == "pass" for item in rounds),
            "interruption_rounds_failed": sum(item["status"] != "pass" for item in rounds),
            "interruption_assertions": sum(item["assertions"] for item in rounds),
        },
        "http_scenarios": http_evidence["scenarios"],
        "fixture_sha256": http_evidence["fixture_sha256"],
        "fault_distribution": distribution,
        "interruption_rounds": rounds,
    }
    output = pathlib.Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(output)
    passed = result["summary"]["interruption_rounds_passed"]
    print(f"M1 interruptions: {passed}/{len(rounds)} rounds; "
          f"distribution {'/'.join(str(distribution[point]) for point, _ in FAULT_ROUNDS)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"m1_interrupt_failed: {error}", file=sys.stderr)
        sys.exit(1)
