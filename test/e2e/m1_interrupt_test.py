#!/usr/bin/env python3
"""Twenty deterministic real-process interruption and recovery rounds."""

import argparse
import datetime
import hashlib
import http.client
import json
import os
import pathlib
import platform
import subprocess
import sys

from m1_http_test import (HttpClient, ServerProcess, complete, create_upload,
                          data, digest, login, upload_file, upload_parts)


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
    parser.add_argument("--normal-server-bin", required=True)
    parser.add_argument("--admin-bin", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--http-evidence", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--build-head", required=True)
    parser.add_argument("--build-dirty", choices=("true", "false"), required=True)
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


def sql_string(value):
    if not value or any(not (ch.isascii() and
                            (ch.isalnum() or ch in "-_./")) for ch in value):
        raise ValueError("evidence query text is invalid")
    return "'" + value + "'"


def graph_counts(project_id, directory_id, name, observed_version_id,
                 expected_size, expected_sha256, media_type,
                 scalar=mysql_scalar):
    project = quote(project_id)
    directory = quote(directory_id)
    observed = quote(observed_version_id)
    expected_digest = "'" + expected_sha256 + "'"
    expected_type = sql_string(media_type)
    expected_name = sql_string(name)
    query = (
        "WITH named_files AS ("
        f"SELECT id FROM files WHERE project_id={project} "
        f"AND directory_id={directory} AND name={expected_name}),"
        "scoped_tasks AS ("
        "SELECT t.* FROM upload_tasks t JOIN named_files f "
        "ON f.id=t.target_file_id "
        f"WHERE t.project_id={project} AND t.mode='create_version' "
        f"AND t.observed_current_version_id={observed} "
        f"AND t.expected_size_bytes={expected_size} "
        f"AND t.expected_sha256={expected_digest} "
        f"AND t.media_type={expected_type}),"
        "related_file_ids AS ("
        "SELECT target_file_id AS id FROM scoped_tasks UNION "
        "SELECT v.file_id FROM file_versions v JOIN scoped_tasks t "
        "ON v.content_id=t.id),"
        "scoped_files AS ("
        "SELECT f.id FROM files f JOIN related_file_ids r ON r.id=f.id),"
        "scoped_versions AS ("
        "SELECT v.* FROM file_versions v JOIN scoped_files f ON f.id=v.file_id) "
        "SELECT "
        "(SELECT COUNT(*) FROM scoped_files),"
        "(SELECT COUNT(*) FROM scoped_tasks),"
        "(SELECT COUNT(*) FROM scoped_versions),"
        "(SELECT COUNT(*) FROM scoped_versions v JOIN scoped_tasks t "
        "ON v.content_id=t.id),"
        "(SELECT COUNT(*) FROM processing_jobs j JOIN scoped_versions v "
        "ON j.file_version_id=v.id),"
        "(SELECT COUNT(*) FROM scoped_tasks t JOIN scoped_files f "
        "ON f.id=t.result_file_id JOIN scoped_versions v "
        "ON v.id=t.result_version_id AND v.file_id=f.id "
        "JOIN processing_jobs j ON j.id=t.processing_job_id "
        "AND j.file_version_id=v.id WHERE t.state='completed' "
        "AND v.content_id=t.id)")
    values = [int(value) for value in scalar(query).split("\t")]
    if len(values) != 6:
        raise AssertionError("unexpected graph evidence shape")
    return {"files": values[0], "upload_tasks": values[1],
            "versions": values[2], "target_versions": values[3],
            "processing_jobs": values[4], "completed_bindings": values[5]}


def round_graph_is_singleton(counts, baseline_versions, baseline_jobs):
    return counts == {
        "files": 1,
        "upload_tasks": 1,
        "versions": baseline_versions + 1,
        "target_versions": 1,
        "processing_jobs": baseline_jobs + 1,
        "completed_bindings": 1,
    }


def publication_observation(task_state, target_versions, completed_bindings,
                            http_status, current_sha256, seed_sha256,
                            target_sha256):
    committed = task_state == "completed"
    database_truthful = ((target_versions, completed_bindings) == (1, 1)
                         if committed else
                         (target_versions, completed_bindings) == (0, 0))
    expected_sha256 = target_sha256 if committed else seed_sha256
    http_truthful = http_status == 200 and current_sha256 == expected_sha256
    return {
        "database_target_versions": target_versions,
        "database_completed_bindings": completed_bindings,
        "http_status": http_status,
        "http_current_sha256": current_sha256,
        "expected_current_sha256": expected_sha256,
        "half_published_download": not (database_truthful and http_truthful),
    }


def git_metadata(repo_root):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                          text=True, capture_output=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=repo_root, text=True, capture_output=True, check=True).stdout.strip())
    return {"head": head, "dirty": dirty}


def environment_facts():
    facts = {
        "http_transport": "loopback_tcp",
        "mysql_transport": "private_unix_socket",
        "server": "smartdocs_test_server",
        "python_dependencies": "standard_library_only",
        "operating_system": platform.platform(),
        "cpu_count": os.cpu_count() or 1,
        "python": platform.python_version(),
    }
    for name, command in (
            ("compiler", ["g++", "--version"]),
            ("mysql_client", ["mysql", "--version"]),
            ("mysql_server", ["mysqld", "--version"]),
            ("openssl", ["openssl", "version"])):
        completed = subprocess.run(command, text=True, capture_output=True,
                                   check=False)
        lines = [line.strip() for line in
                 (completed.stdout + completed.stderr).splitlines()
                 if line.strip()]
        if completed.returncode != 0 or not lines:
            raise RuntimeError(f"version query failed: {command[0]}")
        executable, separator, rest = lines[0].partition(" ")
        facts[name] = pathlib.Path(executable).name + separator + rest
    return facts


def write_evidence(result, output):
    output = pathlib.Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def write(path, content):
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    commit = result["commit"]
    environment = {
        "generated_at": result["generated_at"],
        "commit_head": commit["head"],
        "commit_dirty": str(commit["dirty"]).lower(),
        **result["environment"],
    }
    environment_text = "".join(
        f"{name}={environment[name]}\n" for name in sorted(environment))

    summary = result["summary"]
    passed = (summary["http_scenarios_passed"] ==
              summary["http_scenarios_total"] and
              summary["interruption_rounds_passed"] ==
              summary["interruption_rounds_total"] and
              summary["http_scenarios_failed"] == 0 and
              summary["interruption_rounds_failed"] == 0)
    distribution = "\n".join(
        f"- {point}: {result['fault_distribution'].get(point, 0)}"
        for point, _ in FAULT_ROUNDS)
    failures = result.get("failures", [])
    failure_text = ("- None." if not failures else "\n".join(
        f"- `{failure.get('kind', 'unknown')}`: "
        f"{failure.get('error', 'no error detail')}"
        for failure in failures))
    summary_text = (
        "# M1 acceptance summary\n\n"
        f"- Generated: {result['generated_at']}\n"
        f"- Commit: `{commit['head']}` "
        f"({'dirty' if commit['dirty'] else 'clean'})\n"
        f"- Result: **{'PASS' if passed else 'FAIL'}**\n"
        f"- HTTP scenarios: {summary['http_scenarios_passed']}/"
        f"{summary['http_scenarios_total']} passed "
        f"({summary['http_assertions']} assertions)\n"
        f"- Interruption rounds: {summary['interruption_rounds_passed']}/"
        f"{summary['interruption_rounds_total']} passed "
        f"({summary['interruption_assertions']} assertions)\n\n"
        "## Fault distribution\n\n"
        f"{distribution}\n\n"
        "## Failures\n\n"
        f"{failure_text}\n\n"
        "## Known gaps\n\n"
        "- M2 parsing, metadata extraction, and index publication are outside M1.\n"
        "- M4 load, soak, and concurrency measurements are outside this run.\n"
        "- An operator must review `latest/` before promoting it to `verified/`.\n")

    write(output, json.dumps(result, indent=2, sort_keys=True) + "\n")
    write(output.parent / "environment.txt", environment_text)
    write(output.parent / "summary.md", summary_text)


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
    seed_content = f"M1 interruption seed {number:02d} at {point}\n".encode("ascii")
    content = f"M1 interruption round {number:02d} at {point}\n".encode("ascii")
    seed_sha256 = digest(seed_content)
    expected_sha256 = digest(content)
    assertions = 0

    def require(condition, message):
        nonlocal assertions
        assertions += 1
        check(condition, message)

    server.start()
    client.port = server.port
    _, seed_result = upload_file(client, project_id, root_directory_id, name,
                                 seed_content, "text/plain")
    task = create_upload(
        client, project_id, None, None, content, "text/plain",
        seed_result["file_id"], seed_result["version_id"])
    baseline = graph_counts(
        project_id, root_directory_id, name, seed_result["version_id"],
        len(content), expected_sha256, "text/plain")
    require(baseline == {
        "files": 1, "upload_tasks": 1, "versions": 1,
        "target_versions": 0, "processing_jobs": 1,
        "completed_bindings": 0},
        f"round baseline is not isolated: {baseline}")
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
    require(listed["total"] == 1 and
            listed["items"][0]["id"] == seed_result["file_id"],
            "round-owned file set is not uniquely list-visible")
    current = client.request(
        "GET", f"/api/v1/projects/{project_id}/files/"
               f"{seed_result['file_id']}/content")
    current_sha256 = digest(current.body) if current.status == 200 else None
    historical = client.request(
        "GET", f"/api/v1/projects/{project_id}/files/{seed_result['file_id']}/"
               f"versions/{seed_result['version_id']}/content")
    require(historical.status == 200 and digest(historical.body) == seed_sha256,
            "seed version URL changed during target publication")
    restart_counts = graph_counts(
        project_id, root_directory_id, name, seed_result["version_id"],
        len(content), expected_sha256, "text/plain")
    observation = publication_observation(
        restarted["state"], restart_counts["target_versions"],
        restart_counts["completed_bindings"], current.status,
        current_sha256, seed_sha256, expected_sha256)
    require(not observation["half_published_download"],
            f"database/HTTP publication mismatch: {observation}")
    require(restart_counts == ({
        "files": 1, "upload_tasks": 1, "versions": 2,
        "target_versions": 1, "processing_jobs": 2,
        "completed_bindings": 1} if committed_fault else {
        "files": 1, "upload_tasks": 1, "versions": 1,
        "target_versions": 0, "processing_jobs": 1,
        "completed_bindings": 0}),
        f"unexpected restart graph: {restart_counts}")

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
    counts = graph_counts(
        project_id, root_directory_id, name, seed_result["version_id"],
        len(content), expected_sha256, "text/plain")
    duplicate_version_count = max(
        0, counts["versions"] - baseline["versions"] - 1,
        counts["target_versions"] - 1)
    require(round_graph_is_singleton(
                counts, baseline["versions"], baseline["processing_jobs"]),
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
        "half_published_download": observation["half_published_download"],
        "publication_observation_after_restart": observation,
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
    build_checkout = {"head": arguments.build_head,
                      "dirty": arguments.build_dirty == "true"}
    final_checkout = git_metadata(repo_root)
    check(final_checkout == build_checkout,
          f"checkout changed after executable build: {build_checkout} -> {final_checkout}")
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    result = {
        "schema_version": 1,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "commit": final_checkout,
        "build": {
            "checkout": build_checkout,
            "executables": {
                "server": digest(pathlib.Path(arguments.normal_server_bin).read_bytes()),
                "smartdocs-admin": digest(pathlib.Path(arguments.admin_bin).read_bytes()),
                "smartdocs_test_server": digest(
                    pathlib.Path(arguments.server_bin).read_bytes()),
            },
        },
        "environment": environment_facts(),
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
        "failures": [],
    }
    write_evidence(result, arguments.output)
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
