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
import re
import shutil
import struct
import subprocess
import sys
import zlib

from m1_http_test import (HttpClient, ServerProcess, complete, create_upload,
                          data, digest, login, upload_file, upload_parts)
from m1_browser_test import require_pdf_render


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
    parser.add_argument("--supporting-suites", required=True)
    parser.add_argument("--streaming-evidence", required=True)
    parser.add_argument("--browser-evidence", required=True)
    return parser.parse_args()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_browser_evidence(evidence, artifacts):
    """Exact schema and artifact validation, before any interruption process."""
    def need(condition):
        check(condition, 'browser evidence is malformed, incomplete or failing')

    def keys(value, expected):
        need(type(value) is dict and set(value) == set(expected.split()))

    def integer(value, minimum=0):
        need(type(value) is int and value >= minimum)

    def numbers(value):
        need(type(value) is list)
        for number in value:
            integer(number)

    def sha(value):
        need(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)

    try:
        keys(evidence, 'schema_version status assertions browser webdriver headless sandbox scenarios')
        integer(evidence['schema_version'])
        need(evidence['schema_version'] == 1 and evidence['status'] == 'pass')
        integer(evidence['assertions'], 1)
        need(evidence['headless'] == 'new' and evidence['sandbox'] in
             ('enabled', 'disabled_explicit_root_disposable_host'))
        for product in ('browser', 'webdriver'):
            item = evidence[product]
            keys(item, 'executable version')
            need(type(item['executable']) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', item['executable']) is not None)
            need(type(item['version']) is str and re.fullmatch(r'\d+\.\d+\.\d+\.\d+', item['version']) is not None)
        need(evidence['browser']['version'].split('.')[0] == evidence['webdriver']['version'].split('.')[0])
        keys(evidence['scenarios'], 'refresh_resume_reselect protected_pdf_page_navigation')
        resume = evidence['scenarios']['refresh_resume_reselect']
        keys(resume, 'status assertions part_count chunk_size_bytes file_size_bytes '
                     'confirmed_parts_before confirmed_bytes_before confirmed_parts_after_mismatch '
                     'mismatch_put_parts resume_put_parts confirmed_parts_after '
                     'source_sha256 download_sha256 reloads file_id version_id processing_job_id')
        need(resume['status'] == 'pass')
        for name in ('assertions', 'part_count', 'chunk_size_bytes', 'file_size_bytes',
                     'confirmed_bytes_before', 'reloads'):
            integer(resume[name], 1)
        count, chunk, size = resume['part_count'], resume['chunk_size_bytes'], resume['file_size_bytes']
        need(3 <= count <= 1024 and count == (size + chunk - 1) // chunk)
        need(resume['confirmed_bytes_before'] == chunk and resume['reloads'] == 1)
        for name in ('confirmed_parts_before', 'confirmed_parts_after_mismatch',
                     'mismatch_put_parts', 'resume_put_parts', 'confirmed_parts_after'):
            numbers(resume[name])
        need(resume['confirmed_parts_before'] == resume['confirmed_parts_after_mismatch'] == [0])
        need(resume['mismatch_put_parts'] == [])
        need(sorted(resume['resume_put_parts']) == list(range(1, count)))
        need(resume['confirmed_parts_after'] == list(range(count)))
        sha(resume['source_sha256'])
        sha(resume['download_sha256'])
        need(resume['source_sha256'] == resume['download_sha256'])
        for name in ('file_id', 'version_id', 'processing_job_id'):
            need(type(resume[name]) is str and re.fullmatch(r'[0-9a-f]{32}', resume[name]) is not None)
        pdf = evidence['scenarios']['protected_pdf_page_navigation']
        keys(pdf, 'status assertions fixture_sha256 pages')
        need(pdf['status'] == 'pass')
        integer(pdf['assertions'], 1)
        sha(pdf['fixture_sha256'])
        need(type(pdf['pages']) is list and len(pdf['pages']) == 2)
        bases = []
        for number, page in enumerate(pdf['pages'], 1):
            keys(page, 'page src screenshot sha256 size_bytes')
            integer(page['page'], 1)
            integer(page['size_bytes'], 1024)
            sha(page['sha256'])
            need(page['page'] == number and page['screenshot'] == f'pdf-page-{number}.png')
            need(type(page['src']) is str and re.fullmatch(
                r'/api/v1/projects/[0-9a-f]{32}/files/[0-9a-f]{32}/versions/[0-9a-f]{32}/content#page=' + str(number),
                page['src']) is not None)
            bases.append(page['src'].split('#')[0])
            image_path = pathlib.Path(artifacts) / page['screenshot']
            need(not image_path.is_symlink() and image_path.stat().st_size == page['size_bytes'] and
                 page['size_bytes'] <= 16 * 1024 * 1024)
            details = require_pdf_render(image_path.read_bytes(), number)
            need(details == {key: page[key] for key in ('sha256', 'size_bytes')})
        need(bases[0] == bases[1] and pdf['pages'][0]['sha256'] != pdf['pages'][1]['sha256'])
        need(evidence['assertions'] == resume['assertions'] + pdf['assertions'])
    except (KeyError, TypeError, ValueError, OSError, struct.error, zlib.error) as error:
        raise AssertionError('browser evidence is malformed or its screenshot is invalid') from error
    return evidence


def parse_suite_counts(kind, output):
    if kind == "cpp":
        match = re.search(
            r"^RESULT (\d+) passed, (\d+) failed, (\d+) skipped$",
            output, re.MULTILINE)
        if match:
            passed, failed, skipped = map(int, match.groups())
            total = passed + failed + skipped
            if total > 0:
                return {"passed": passed, "failed": failed, "skipped": skipped,
                        "total": total}
    elif kind == "unittest":
        ran = re.search(r"^Ran (\d+) tests? in ", output, re.MULTILINE)
        status = re.search(r"^(OK|FAILED)(?: \(([^)]*)\))?$", output,
                           re.MULTILINE)
        if ran and status:
            details = {name.strip(): int(value) for name, value in re.findall(
                r"([a-z ]+)=([0-9]+)", status.group(2) or "")}
            failed = (details.get("failures", 0) + details.get("errors", 0) +
                      details.get("unexpected successes", 0))
            skipped = (details.get("skipped", 0) +
                       details.get("expected failures", 0))
            total = int(ran.group(1))
            status_matches = ((status.group(1) == "OK" and failed == 0) or
                              (status.group(1) == "FAILED" and failed > 0))
            if total > 0 and status_matches and failed + skipped <= total:
                return {"passed": total - failed - skipped,
                        "failed": failed, "skipped": skipped, "total": total}
    elif kind == "node":
        fields = {name: int(value) for name, value in re.findall(
            r"^(?:#|ℹ) (tests|pass|fail|cancelled|skipped|todo) (\d+)$",
            output, re.MULTILINE)}
        if set(fields) == {"tests", "pass", "fail", "cancelled", "skipped",
                           "todo"}:
            counts = {"passed": fields["pass"],
                      "failed": fields["fail"] + fields["cancelled"],
                      "skipped": fields["skipped"] + fields["todo"],
                      "total": fields["tests"]}
            if counts["total"] > 0 and \
                    sum(counts[name] for name in
                        ("passed", "failed", "skipped")) == \
                    counts["total"]:
                return counts
    else:
        raise ValueError(f"unknown suite output kind: {kind}")
    raise ValueError(f"malformed {kind} suite output")


def supporting_suites_pass(suites):
    expected = {"cpp_unit_integration", "ui_contract", "ui_behavior"}
    return set(suites) == expected and all(
        suite["total"] > 0 and suite["failed"] == 0 and
        suite["skipped"] == 0 and suite["passed"] == suite["total"]
        for suite in suites.values())


def validate_streaming_evidence(evidence):
    expected_keys = {
        "schema_version", "status", "file_size_bytes",
        "upload_chunk_size_bytes", "phases", "hashes",
        "rss", "assertions",
    }
    expected_phases = [
        "concurrent_streamed_part_upload",
        "concurrent_upload_completion_integrity_verification",
        "concurrent_streamed_download",
    ]
    try:
        check(isinstance(evidence, dict) and set(evidence) == expected_keys,
              "streaming measurement schema is malformed")
        check(evidence["schema_version"] == 2 and evidence["status"] == "pass",
              "streaming measurement did not pass")
        check(evidence["file_size_bytes"] == 33554432 and
              evidence["upload_chunk_size_bytes"] == 8388608,
              "streaming measurement dimensions are wrong")
        phases = evidence["phases"]
        check(isinstance(phases, list) and len(phases) == 3,
              "streaming measurement phases are incomplete")
        for phase, name, request_count in zip(
                phases, expected_phases, (16, 4, 4)):
            check(set(phase) == {"name", "configured_concurrency",
                                 "observed_max_concurrency", "request_count"} and
                  phase["name"] == name and
                  phase["configured_concurrency"] == 4 and
                  phase["observed_max_concurrency"] == 4 and
                  phase["request_count"] == request_count,
                  "streaming measurement phase concurrency is malformed")
        hashes = evidence["hashes"]
        check(isinstance(hashes, list) and len(hashes) == 4,
              "streaming measurement hashes are malformed")
        for index, item in enumerate(hashes):
            check(set(item) == {"file", "source_sha256", "download_sha256"} and
                  item["file"] == f"streaming-{index}.bin" and
                  re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"]) is not None and
                  item["download_sha256"] == item["source_sha256"],
                  "streaming measurement transfer hash mismatch")
        rss = evidence["rss"]
        check(set(rss) == {"baseline_vm_rss_bytes", "baseline_vm_hwm_bytes",
                          "final_peak_vm_hwm_bytes", "peak_growth_bytes",
                          "growth_limit_bytes"} and
              all(type(rss[name]) is int and rss[name] >= 0 for name in rss),
              "streaming measurement RSS fields are malformed")
        check(rss["baseline_vm_hwm_bytes"] >= rss["baseline_vm_rss_bytes"] and
              rss["final_peak_vm_hwm_bytes"] >= rss["baseline_vm_hwm_bytes"] and
              rss["peak_growth_bytes"] ==
              rss["final_peak_vm_hwm_bytes"] - rss["baseline_vm_rss_bytes"] and
              rss["growth_limit_bytes"] == 67108864 and
              rss["peak_growth_bytes"] < rss["growth_limit_bytes"],
              "streaming measurement RSS growth limit was not met")
        check(type(evidence["assertions"]) is int and evidence["assertions"] > 0,
              "streaming measurement assertion count is malformed")
    except (KeyError, TypeError, AttributeError) as error:
        raise AssertionError("streaming measurement schema is malformed") from error
    return evidence


def validate_supporting_suites(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpp-suite-output", required=True)
    parser.add_argument("--ui-contract-output", required=True)
    parser.add_argument("--ui-behavior-output", required=True)
    paths = parser.parse_args(arguments)
    suites = {
        "cpp_unit_integration": parse_suite_counts(
            "cpp", pathlib.Path(paths.cpp_suite_output).read_text(
                encoding="utf-8")),
        "ui_contract": parse_suite_counts(
            "unittest", pathlib.Path(paths.ui_contract_output).read_text(
                encoding="utf-8")),
        "ui_behavior": parse_suite_counts(
            "node", pathlib.Path(paths.ui_behavior_output).read_text(
                encoding="utf-8")),
    }
    check(supporting_suites_pass(suites),
          f"supporting suite failure: {suites}")
    print(json.dumps(suites, sort_keys=True))
    return 0


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
            ("node", ["node", "--version"]),
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
        'browser': result['browser']['browser']['executable'],
        'browser_version': result['browser']['browser']['version'],
        'webdriver': result['browser']['webdriver']['executable'],
        'webdriver_version': result['browser']['webdriver']['version'],
        'browser_headless': result['browser']['headless'],
        'browser_sandbox': result['browser']['sandbox'],
    }
    environment_text = "".join(
        f"{name}={environment[name]}\n" for name in sorted(environment))

    summary = result["summary"]
    passed = (summary["http_scenarios_passed"] ==
              summary["http_scenarios_total"] and
              summary["interruption_rounds_passed"] ==
              summary["interruption_rounds_total"] and
              summary["http_scenarios_failed"] == 0 and
              summary["interruption_rounds_failed"] == 0 and
              supporting_suites_pass(result["supporting_suites"]) and
              result["streaming_measurement"]["status"] == "pass" and
              result['browser']['status'] == 'pass')
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
        f"- Browser scenarios: 2/2 passed ({result['browser']['assertions']} assertions); "
        f"{result['browser']['browser']['executable']} {result['browser']['browser']['version']}; "
        f"WebDriver {result['browser']['webdriver']['version']}; "
        f"headless={result['browser']['headless']}, sandbox={result['browser']['sandbox']}\n"
        f"- Interruption rounds: {summary['interruption_rounds_passed']}/"
        f"{summary['interruption_rounds_total']} passed "
        f"({summary['interruption_assertions']} assertions)\n"
        f"- Streaming measurement: "
        f"{result['streaming_measurement']['status'].upper()}, peak growth "
        f"{result['streaming_measurement']['rss']['peak_growth_bytes']}/"
        f"{result['streaming_measurement']['rss']['growth_limit_bytes']} bytes "
        f"({result['streaming_measurement']['assertions']} assertions)\n"
        f"- C++ unit/integration: "
        f"{result['supporting_suites']['cpp_unit_integration']['passed']}/"
        f"{result['supporting_suites']['cpp_unit_integration']['total']} passed, "
        f"{result['supporting_suites']['cpp_unit_integration']['failed']} failed, "
        f"{result['supporting_suites']['cpp_unit_integration']['skipped']} skipped\n"
        f"- UI contract: {result['supporting_suites']['ui_contract']['passed']}/"
        f"{result['supporting_suites']['ui_contract']['total']} passed, "
        f"{result['supporting_suites']['ui_contract']['failed']} failed, "
        f"{result['supporting_suites']['ui_contract']['skipped']} skipped\n"
        f"- UI behavior: {result['supporting_suites']['ui_behavior']['passed']}/"
        f"{result['supporting_suites']['ui_behavior']['total']} passed, "
        f"{result['supporting_suites']['ui_behavior']['failed']} failed, "
        f"{result['supporting_suites']['ui_behavior']['skipped']} skipped\n\n"
        "## Fault distribution\n\n"
        f"{distribution}\n\n"
        "## Failures\n\n"
        f"{failure_text}\n\n"
        "## Known gaps\n\n"
        "- M2 parsing, metadata extraction, and index publication are outside M1.\n"
        "- M4 load, soak, and concurrency measurements are outside this run.\n"
        "- An operator must review `latest/` before promoting it to `verified/`.\n")

    write(output, json.dumps(result, indent=2, sort_keys=True) + "\n")
    write(output.parent / "streaming.json", json.dumps(
        result["streaming_measurement"], indent=2, sort_keys=True) + "\n")
    write(output.parent / 'browser.json', json.dumps(
        result['browser'], indent=2, sort_keys=True) + '\n')
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
    supporting_suites = json.loads(pathlib.Path(
        arguments.supporting_suites).read_text(encoding="utf-8"))
    check(supporting_suites_pass(supporting_suites),
          f"supporting suite failure: {supporting_suites}")
    streaming_measurement = validate_streaming_evidence(json.loads(pathlib.Path(
        arguments.streaming_evidence).read_text(encoding="utf-8")))
    browser_path = pathlib.Path(arguments.browser_evidence)
    browser_artifacts = browser_path.parent / 'browser-artifacts'
    browser_evidence = validate_browser_evidence(
        json.loads(browser_path.read_text(encoding='utf-8')), browser_artifacts)
    check(browser_evidence['scenarios']['protected_pdf_page_navigation']['fixture_sha256'] ==
          http_evidence['fixture_sha256'].get('sample.pdf'),
          'browser evidence PDF fixture differs from HTTP fixture')
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
        "schema_version": 4,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "commit": final_checkout,
        "build": {
            "checkout": build_checkout,
            "executables": {
                "server": digest(pathlib.Path(arguments.normal_server_bin).read_bytes()),
                "smartdocs-admin": digest(pathlib.Path(arguments.admin_bin).read_bytes()),
                "smartdocs_tests": digest(
                    (pathlib.Path(repo_root) / "bin/smartdocs_tests").read_bytes()),
                "smartdocs_test_server": digest(
                    pathlib.Path(arguments.server_bin).read_bytes()),
            },
        },
        "environment": {
            **environment_facts(),
            'browser': browser_evidence['browser']['executable'],
            'browser_version': browser_evidence['browser']['version'],
            'webdriver': browser_evidence['webdriver']['executable'],
            'webdriver_version': browser_evidence['webdriver']['version'],
            'browser_headless': browser_evidence['headless'],
            'browser_sandbox': browser_evidence['sandbox'],
        },
        "summary": {
            "http_scenarios_total": len(http_evidence["scenarios"]),
            "http_scenarios_passed": http_evidence["passed"],
            "http_scenarios_failed": http_evidence["failed"],
            "http_assertions": http_evidence["assertions"],
            'browser_assertions': browser_evidence['assertions'],
            "interruption_rounds_total": len(rounds),
            "interruption_rounds_passed": sum(item["status"] == "pass" for item in rounds),
            "interruption_rounds_failed": sum(item["status"] != "pass" for item in rounds),
            "interruption_assertions": sum(item["assertions"] for item in rounds),
        },
        "supporting_suites": supporting_suites,
        "streaming_measurement": streaming_measurement,
        'browser': browser_evidence,
        "http_scenarios": http_evidence["scenarios"],
        "fixture_sha256": http_evidence["fixture_sha256"],
        "fault_distribution": distribution,
        "interruption_rounds": rounds,
        "failures": [],
    }
    destination = pathlib.Path(arguments.output).parent / 'browser-artifacts'
    destination.mkdir(parents=True, exist_ok=True)
    for page in browser_evidence['scenarios']['protected_pdf_page_navigation']['pages']:
        if (browser_artifacts / page['screenshot']).resolve() != (destination / page['screenshot']).resolve():
            shutil.copyfile(browser_artifacts / page['screenshot'], destination / page['screenshot'])
    validate_browser_evidence(browser_evidence, destination)
    write_evidence(result, arguments.output)
    passed = result["summary"]["interruption_rounds_passed"]
    print(f"M1 interruptions: {passed}/{len(rounds)} rounds; "
          f"distribution {'/'.join(str(distribution[point]) for point, _ in FAULT_ROUNDS)}")
    return 0


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--validate-supporting-suites"]:
            sys.exit(validate_supporting_suites(sys.argv[2:]))
        sys.exit(main())
    except Exception as error:
        print(f"m1_interrupt_failed: {error}", file=sys.stderr)
        sys.exit(1)
