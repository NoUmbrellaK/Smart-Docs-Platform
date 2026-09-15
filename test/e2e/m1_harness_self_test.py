#!/usr/bin/env python3
"""Focused checks that keep the M1 evidence harness from reporting false green."""

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import m1_interrupt_test as harness


class HarnessEvidenceTest(unittest.TestCase):
    def test_supporting_suite_counts_are_parsed_and_gate_evidence(self):
        suites = {
            "cpp_unit_integration": harness.parse_suite_counts(
                "cpp", "RESULT 40 passed, 0 failed, 0 skipped\n"),
            "ui_contract": harness.parse_suite_counts(
                "unittest",
                ".......\nRan 7 tests in 0.021s\n\nOK\n"),
            "ui_behavior": harness.parse_suite_counts(
                "node",
                "✔ test/e2e/ui_behavior_test.mjs (130ms)\n"
                "ℹ tests 18\nℹ suites 0\nℹ pass 17\nℹ fail 0\n"
                "ℹ cancelled 0\nℹ skipped 1\nℹ todo 0\n"),
        }
        self.assertEqual(suites, {
            "cpp_unit_integration": {
                "passed": 40, "failed": 0, "skipped": 0, "total": 40},
            "ui_contract": {
                "passed": 7, "failed": 0, "skipped": 0, "total": 7},
            "ui_behavior": {
                "passed": 17, "failed": 0, "skipped": 1, "total": 18},
        })
        with self.assertRaisesRegex(ValueError, "malformed cpp suite output"):
            harness.parse_suite_counts("cpp", "all good\n")
        with self.assertRaisesRegex(ValueError, "malformed unittest suite output"):
            harness.parse_suite_counts("unittest", "Ran 0 tests in 0.001s\n\nOK\n")
        self.assertEqual(
            harness.parse_suite_counts(
                "unittest", "Fs.\nRan 3 tests in 0.001s\n\n"
                "FAILED (skipped=1, unexpected successes=1)\n"),
            {"passed": 1, "failed": 1, "skipped": 1, "total": 3})
        for terminal in ("FAILED", "FAILED (skipped=1)",
                         "FAILED (failures=4)"):
            with self.subTest(terminal=terminal):
                with self.assertRaisesRegex(
                        ValueError, "malformed unittest suite output"):
                    harness.parse_suite_counts(
                        "unittest", f"Ran 3 tests in 0.001s\n\n{terminal}\n")

        result = {
            "schema_version": 1,
            "generated_at": "2026-09-15T01:02:03Z",
            "commit": {"head": "1" * 40, "dirty": False},
            "environment": {},
            "summary": {
                "http_scenarios_total": 9,
                "http_scenarios_passed": 9,
                "http_scenarios_failed": 0,
                "http_assertions": 40,
                "interruption_rounds_total": 20,
                "interruption_rounds_passed": 20,
                "interruption_rounds_failed": 0,
                "interruption_assertions": 280,
            },
            "supporting_suites": suites,
            "fault_distribution": dict(harness.FAULT_ROUNDS),
            "failures": [],
        }
        with tempfile.TemporaryDirectory(prefix="m1-suite-evidence.") as root:
            output = pathlib.Path(root) / "results.json"
            harness.write_evidence(result, output)
            summary = (output.parent / "summary.md").read_text(encoding="utf-8")
        self.assertIn("Result: **FAIL**", summary)
        self.assertIn("C++ unit/integration: 40/40 passed, 0 failed, 0 skipped", summary)
        self.assertIn("UI contract: 7/7 passed, 0 failed, 0 skipped", summary)
        self.assertIn("UI behavior: 17/18 passed, 0 failed, 1 skipped", summary)

    def test_environment_facts_record_required_tool_versions(self):
        facts = harness.environment_facts()

        self.assertTrue(facts["compiler"].startswith("g++ "))
        self.assertTrue(facts["mysql_client"].startswith("mysql "))
        self.assertTrue(facts["mysql_server"].startswith("mysqld "))
        self.assertTrue(facts["openssl"].startswith("OpenSSL "))
        self.assertTrue(facts["node"].startswith("v"))

    def test_write_evidence_emits_reviewable_task12_artifacts(self):
        result = {
            "schema_version": 1,
            "generated_at": "2026-09-15T01:02:03Z",
            "commit": {"head": "1" * 40, "dirty": False},
            "environment": {
                "compiler": "g++ fixture 1.0",
                "mysql_client": "mysql fixture 2.0",
                "mysql_server": "mysqld fixture 2.0",
                "node": "v24.18.0",
                "openssl": "OpenSSL fixture 3.0",
                "cpu_count": 2,
            },
            "summary": {
                "http_scenarios_total": 9,
                "http_scenarios_passed": 8,
                "http_scenarios_failed": 1,
                "http_assertions": 40,
                "interruption_rounds_total": 20,
                "interruption_rounds_passed": 19,
                "interruption_rounds_failed": 1,
                "interruption_assertions": 280,
            },
            "supporting_suites": {
                "cpp_unit_integration": {
                    "passed": 42, "failed": 0, "skipped": 1, "total": 43},
                "ui_contract": {
                    "passed": 8, "failed": 0, "skipped": 0, "total": 8},
                "ui_behavior": {
                    "passed": 18, "failed": 0, "skipped": 0, "total": 18},
            },
            "fault_distribution": {
                "AfterPartTempFsync": 4,
                "AfterAssembledFsync": 4,
                "AfterObjectRename": 4,
                "BeforeDatabaseCommit": 3,
                "AfterDatabaseCommit": 3,
                "BeforeHttpResponse": 2,
            },
            "failures": [
                {"kind": "http_scenario", "id": "T-09",
                 "error": "fixture failure"},
                {"kind": "interruption_round", "round": 20,
                 "fault_point": "BeforeHttpResponse",
                 "error": "fixture interruption failure"},
            ],
        }

        with tempfile.TemporaryDirectory(prefix="m1-evidence-writer.") as root:
            output = pathlib.Path(root) / "results.json"
            harness.write_evidence(result, output)

            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), result)
            environment = (output.parent / "environment.txt").read_text(
                encoding="utf-8")
            summary = (output.parent / "summary.md").read_text(encoding="utf-8")
            self.assertIn("commit_head=" + "1" * 40, environment)
            self.assertIn("compiler=g++ fixture 1.0", environment)
            self.assertIn("mysql_client=mysql fixture 2.0", environment)
            self.assertIn("mysql_server=mysqld fixture 2.0", environment)
            self.assertIn("node=v24.18.0", environment)
            self.assertIn("openssl=OpenSSL fixture 3.0", environment)
            self.assertIn("Result: **FAIL**", summary)
            self.assertIn("HTTP scenarios: 8/9 passed (40 assertions)", summary)
            self.assertIn("Interruption rounds: 19/20 passed (280 assertions)", summary)
            self.assertIn("C++ unit/integration: 42/43 passed, 0 failed, 1 skipped",
                          summary)
            self.assertIn("AfterPartTempFsync: 4", summary)
            self.assertIn("fixture interruption failure", summary)
            self.assertIn("Known gaps", summary)
            self.assertNotIn(root, environment + summary)

    def test_round_scope_counts_extra_ids_without_mixing_other_rounds(self):
        project = "1" * 32
        directory = "2" * 32
        file_id = "3" * 32
        seed_version = "4" * 32
        seed_job = "5" * 32
        task_id = "6" * 32
        target_version = "7" * 32
        target_job = "8" * 32
        other_file = "9" * 32
        other_version = "a" * 32
        other_job = "b" * 32
        leaked_file = "c" * 32
        leaked_version = "d" * 32
        leaked_job = "e" * 32
        duplicate_task = "f" * 32
        duplicate_version = "0" * 32
        duplicate_job = "1" * 32
        target_sha256 = "f" * 64

        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)
        database.executescript("""
            CREATE TABLE files(id, project_id, directory_id, name);
            CREATE TABLE upload_tasks(
              id, project_id, target_file_id, mode,
              observed_current_version_id, expected_size_bytes,
              expected_sha256, media_type, result_file_id,
              result_version_id, processing_job_id, state);
            CREATE TABLE file_versions(id, file_id, content_id);
            CREATE TABLE processing_jobs(id, file_version_id);
        """)
        database.executemany(
            "INSERT INTO files VALUES (?, ?, ?, ?)",
            [(file_id, project, directory, "interrupt-01.txt"),
             (other_file, project, directory, "interrupt-previous.txt")])
        database.executemany(
            "INSERT INTO file_versions VALUES (?, ?, ?)",
            [(seed_version, file_id, "0" * 32),
             (other_version, other_file, "a" * 32)])
        database.executemany(
            "INSERT INTO processing_jobs VALUES (?, ?)",
            [(seed_job, seed_version), (other_job, other_version)])
        task = (task_id, project, file_id, "create_version", seed_version,
                33, target_sha256, "text/plain", None, None, None,
                "uploading")
        database.execute("INSERT INTO upload_tasks VALUES (?, ?, ?, ?, ?, ?, "
                         "?, ?, ?, ?, ?, ?)", task)

        def scalar(query):
            return "\t".join(str(value) for value in database.execute(query).fetchone())

        counts = harness.graph_counts(
            project, directory, "interrupt-01.txt", seed_version,
            33, target_sha256, "text/plain", scalar)
        self.assertEqual(
            counts,
            {"files": 1, "upload_tasks": 1, "versions": 1,
             "target_versions": 0, "processing_jobs": 1,
             "completed_bindings": 0})

        database.execute("INSERT INTO file_versions VALUES (?, ?, ?)",
                         (target_version, file_id, task_id))
        database.execute("INSERT INTO processing_jobs VALUES (?, ?)",
                         (target_job, target_version))
        database.execute(
            "UPDATE upload_tasks SET result_file_id=?, result_version_id=?, "
            "processing_job_id=?, state='completed' WHERE id=?",
            (file_id, target_version, target_job, task_id))
        counts = harness.graph_counts(
            project, directory, "interrupt-01.txt", seed_version,
            33, target_sha256, "text/plain", scalar)
        self.assertTrue(harness.round_graph_is_singleton(counts, 1, 1))

        # A retry must not be able to attach this round's new content to a
        # different file ID.  It is deliberately outside the round's name and
        # directory so name-scoped counting would falsely stay green.
        database.execute("INSERT INTO files VALUES (?, ?, ?, ?)",
                         (leaked_file, project, "x" * 32, "escaped.txt"))
        database.execute("INSERT INTO file_versions VALUES (?, ?, ?)",
                         (leaked_version, leaked_file, task_id))
        database.execute("INSERT INTO processing_jobs VALUES (?, ?)",
                         (leaked_job, leaked_version))

        counts = harness.graph_counts(
            project, directory, "interrupt-01.txt", seed_version,
            33, target_sha256, "text/plain", scalar)
        self.assertEqual(counts["files"], 2)
        self.assertEqual(counts["target_versions"], 2)
        self.assertEqual(counts["processing_jobs"], 3)
        self.assertFalse(harness.round_graph_is_singleton(counts, 1, 1))

        database.execute("INSERT INTO upload_tasks VALUES (?, ?, ?, ?, ?, ?, "
                         "?, ?, ?, ?, ?, ?)",
                         (duplicate_task, project, file_id, "create_version",
                          seed_version, 33, target_sha256, "text/plain", file_id,
                          duplicate_version, duplicate_job, "completed"))
        database.execute("INSERT INTO file_versions VALUES (?, ?, ?)",
                         (duplicate_version, file_id, duplicate_task))
        database.execute("INSERT INTO processing_jobs VALUES (?, ?)",
                         (duplicate_job, duplicate_version))
        counts = harness.graph_counts(
            project, directory, "interrupt-01.txt", seed_version,
            33, target_sha256, "text/plain", scalar)
        self.assertEqual(
            counts,
            {"files": 2, "upload_tasks": 2, "versions": 4,
             "target_versions": 3, "processing_jobs": 4,
             "completed_bindings": 2})
        self.assertFalse(harness.round_graph_is_singleton(counts, 1, 1))

    def test_half_publication_is_derived_from_database_and_http(self):
        derive = getattr(harness, "publication_observation", None)
        self.assertIsNotNone(
            derive,
            "constant false cannot expose uncommitted new bytes from the real file URL")
        seed = "1" * 64
        target = "2" * 64

        # Run the real round logic against a response double that exposes
        # uncommitted bytes at the actual file URL. The round must request that
        # URL and reject the database/HTTP mismatch before retrying.
        point = "AfterObjectRename"
        number = 1
        file_id = "3" * 32
        version_id = "4" * 32
        job_id = "5" * 32
        task_id = "6" * 32
        seed_content = f"M1 interruption seed {number:02d} at {point}\n".encode("ascii")
        content = f"M1 interruption round {number:02d} at {point}\n".encode("ascii")

        class Response:
            def __init__(self, status, payload=None, body=b""):
                self.status = status
                self._payload = payload or {}
                self.body = body

            def json(self):
                return {"data": self._payload}

        class LeakyClient:
            def __init__(self):
                self.port = 0
                self.leaked_url_requested_before_retry = False
                self.complete_calls = 0

            def request(self, method, path, body=None, headers=None):
                if method == "POST" and path.endswith("/complete"):
                    self.complete_calls += 1
                    return Response(0)
                if path.endswith(f"/uploads/{task_id}"):
                    return Response(200, {
                        "state": "interrupted",
                        "confirmed_parts": [{"part_number": 0}],
                        "file_id": None,
                        "version_id": None,
                        "processing_job_id": None})
                if "?name=" in path:
                    return Response(200, {"total": 1,
                                          "items": [{"id": file_id}]})
                if path.endswith(f"/files/{task_id}/content"):
                    return Response(404)
                if path.endswith(
                        f"/files/{file_id}/versions/{version_id}/content"):
                    return Response(200, body=seed_content)
                if path.endswith(f"/files/{file_id}/content"):
                    self.leaked_url_requested_before_retry = True
                    return Response(200, body=content)
                raise AssertionError(f"unexpected driver request: {method} {path}")

        class FaultedServer:
            def __init__(self):
                self.port = 1

            def start(self, fault_point=None):
                self.port += 1

            def stop(self):
                pass

            def wait_for_fault_exit(self):
                return 86

        task = {"task_id": task_id, "chunk_size": len(content), "part_count": 1}
        baseline_counts = {
            "files": 1, "upload_tasks": 1, "versions": 1,
            "target_versions": 0, "processing_jobs": 1,
            "completed_bindings": 0}
        replacements = {
            "upload_file": lambda *unused: (
                task, {"file_id": file_id, "version_id": version_id,
                       "processing_job_id": job_id}),
            "create_upload": lambda *unused: task,
            "upload_parts": lambda *unused: None,
            "graph_counts": lambda *unused, **unused_keywords: baseline_counts,
        }
        for name, replacement in replacements.items():
            original = getattr(harness, name)
            setattr(harness, name, replacement)
            self.addCleanup(setattr, harness, name, original)

        client = LeakyClient()
        with self.assertRaisesRegex(AssertionError,
                                    "database/HTTP publication mismatch"):
            harness.run_round(
                FaultedServer(), client, "7" * 32, "8" * 32, point, number)
        self.assertTrue(client.leaked_url_requested_before_retry)

        leaked = derive("interrupted", 0, 0, 200, target, seed, target)
        self.assertTrue(leaked["half_published_download"])
        hidden = derive("interrupted", 0, 0, 200, seed, seed, target)
        self.assertFalse(hidden["half_published_download"])
        committed = derive("completed", 1, 1, 200, target, seed, target)
        self.assertFalse(committed["half_published_download"])
        incomplete = derive("completed", 1, 1, 200, seed, seed, target)
        self.assertTrue(incomplete["half_published_download"])

    def test_runner_rebuilds_stale_executables_and_binds_checkout(self):
        with tempfile.TemporaryDirectory(prefix="m1-runner-self-test.") as root:
            repo = pathlib.Path(root)
            for directory in (
                    "test/e2e", "test/fixtures/m1", "scripts", "bin",
                    "docs/evidence/m1/latest", "fake-bin"):
                (repo / directory).mkdir(parents=True, exist_ok=True)
            runner = repo / "test/e2e/run_m1.sh"
            (repo / "test/fixtures/m1/plain.txt").write_text(
                "plain\n", encoding="utf-8")
            (repo / "test/fixtures/m1/sample.pdf").write_bytes(b"%PDF-1.4\n")
            self._write_executable(repo / "scripts/migrate.sh", "#!/bin/sh\nexit 0\n")
            for artifact in ("server", "smartdocs-admin", "smartdocs_test_server",
                             "smartdocs_tests"):
                self._write_executable(repo / "bin" / artifact, "STALE\n")

            fake_bin = repo / "fake-bin"
            self._write_executable(
                fake_bin / "git",
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *'rev-parse HEAD'*) printf '%064d\\n' 0 ;;\n"
                "  *'status --porcelain'*) exit 0 ;;\n"
                "  *) exit 2 ;;\n"
                "esac\n")
            self._write_executable(
                fake_bin / "make",
                "#!/bin/sh\n"
                "set -eu\n"
                "arguments=$*\n"
                "repo=''\n"
                "clean=false\n"
                "jobs=''\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    -C) repo=$2; shift 2 ;;\n"
                "    -j*) jobs=$1; shift ;;\n"
                "    clean) clean=true; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if $clean; then\n"
                "  rm -f \"$repo/bin/server\" \"$repo/bin/smartdocs-admin\" "
                "\"$repo/bin/smartdocs_test_server\" \"$repo/bin/smartdocs_tests\"\n"
                "else\n"
                "  printf '%s\\n' \"$jobs\" >\"$repo/build-jobs\"\n"
                "  printf '%s\\n' \"$arguments\" >\"$repo/build-arguments\"\n"
                "  mkdir -p \"$repo/bin\"\n"
                "  for artifact in server smartdocs-admin smartdocs_test_server "
                "smartdocs_tests; do\n"
                "    if [ \"$artifact\" = server ]; then\n"
                "      printf '#!/bin/sh\\n# FRESH\\nwhile :; do sleep 1; done\\n' "
                ">\"$repo/bin/$artifact\"\n"
                "    elif [ \"$artifact\" = smartdocs_tests ]; then\n"
                "      printf '#!/bin/sh\\n# FRESH\\n"
                "if [ \"${SMARTDOCS_TEST_MYSQL:-}\" != 1 ]; then "
                "printf \"SMARTDOCS_TEST_MYSQL missing\\\\n\" >&2; exit 65; fi\\n"
                "printf marker >\"${FAKE_MARKER_ROOT}/cpp-suite-ran\"\\n"
                "if [ \"${FAKE_CPP_FAILURE:-}\" = 1 ]; then "
                "printf \"RESULT 42 passed, 1 failed, 0 skipped\\\\n\"; exit 1; fi\\n"
                "printf \"RESULT 43 passed, 0 failed, 0 skipped\\\\n\"\\n' "
                ">\"$repo/bin/$artifact\"\n"
                "    else\n"
                "      printf 'FRESH\\n' >\"$repo/bin/$artifact\"\n"
                "    fi\n"
                "    chmod +x \"$repo/bin/$artifact\"\n"
                "  done\n"
                "fi\n")
            self._write_executable(
                fake_bin / "mysqld",
                "#!/bin/sh\n"
                "case \"$*\" in *--initialize-insecure*) exit 0 ;; esac\n"
                "trap 'exit 0' TERM INT\n"
                "while :; do sleep 1; done\n")
            self._write_executable(fake_bin / "mysql", "#!/bin/sh\nexit 0\n")
            self._write_executable(fake_bin / "mysqladmin", "#!/bin/sh\nexit 0\n")
            self._write_executable(
                fake_bin / "node",
               "#!/bin/sh\n"
                "if [ \"${1:-}\" = --version ]; then printf 'v24.18.0\\n'; exit 0; fi\n"
                "printf marker >\"${FAKE_MARKER_ROOT}/ui-behavior-ran\"\n"
               "tests=18\n"
                "if [ \"${1:-}\" = --test ]; then tests=1; fi\n"
                "printf 'ℹ tests %s\\nℹ suites 0\\nℹ pass %s\\nℹ fail 0\\n"
                "ℹ cancelled 0\\nℹ skipped 0\\nℹ todo 0\\n' \"$tests\" \"$tests\"\n")
            self._write_executable(
                fake_bin / "python3",
                "#!/usr/bin/python3\n"
                "import json, os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if args and args[0] == '-B': args.pop(0)\n"
                "if args and args[0] == '-':\n"
                "    os.execv('/usr/bin/python3', ['/usr/bin/python3', '-B'] + args)\n"
                "script = args.pop(0) if args else ''\n"
                "if script.endswith('m1_http_test.py') and "
                "'--normal-server-bin' in args:\n"
                "    print('unexpected normal-server binding', file=sys.stderr)\n"
                "    sys.exit(64)\n"
                "if script.endswith('ui_contract_test.py'):\n"
                "    pathlib.Path(os.environ['FAKE_MARKER_ROOT'], "
                "'ui-contract-ran').write_text('marker')\n"
                "    print('........\\nRan 8 tests in 0.01s\\n\\nOK')\n"
                "if script.endswith('m1_interrupt_test.py'):\n"
                "    if '--validate-supporting-suites' in args:\n"
                "        print(json.dumps({'cpp_unit_integration': "
                "{'passed': 43, 'failed': 0, 'skipped': 0, 'total': 43}, "
                "'ui_contract': {'passed': 8, 'failed': 0, 'skipped': 0, "
                "'total': 8}, 'ui_behavior': {'passed': 18, 'failed': 0, "
                "'skipped': 0, 'total': 18}}))\n"
                "        sys.exit(0)\n"
                "    required = {'--normal-server-bin', '--admin-bin', "
                "'--build-head', '--build-dirty', '--supporting-suites'}\n"
                "    bound = required.issubset(args)\n"
                "    if bound:\n"
                "        bound = (args[args.index('--build-head') + 1] == '0' * 64 "
                "and args[args.index('--build-dirty') + 1] == 'false')\n"
                "    suites = (bound and pathlib.Path("
                "args[args.index('--supporting-suites') + 1]).is_file())\n"
                "    output = pathlib.Path(args[args.index('--output') + 1])\n"
                "    output.parent.mkdir(parents=True, exist_ok=True)\n"
                "    output.write_text(json.dumps({'received_build_binding': bound, "
                "'received_supporting_suites': suites, "
                "'summary': {"
                "'http_scenarios_passed': 9, 'http_scenarios_total': 9, "
                "'interruption_rounds_passed': 20, "
                "'interruption_rounds_total': 20, 'http_assertions': 40, "
                "'interruption_assertions': 280}}), encoding='utf-8')\n")

            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
            environment["FAKE_MARKER_ROOT"] = str(repo)
            shutil.copy2(pathlib.Path(__file__).with_name("run_m1.sh"), runner)
            failing_environment = environment.copy()
            failing_environment["FAKE_CPP_FAILURE"] = "1"
            failed = subprocess.run(
                [str(runner)], cwd=repo, env=failing_environment, text=True,
                capture_output=True, check=False)
            self.assertNotEqual(failed.returncode, 0)
            for marker in ("cpp-suite-ran", "ui-contract-ran", "ui-behavior-ran"):
                self.assertTrue((repo / marker).is_file(),
                                f"supporting suite did not run: {marker}")
            self.assertIn("RESULT 42 passed, 1 failed, 0 skipped", failed.stderr)
            self.assertIn("supporting_suite_status", failed.stderr)
            completed = subprocess.run(
                [str(runner)], cwd=repo, env=environment, text=True,
                capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            problems = []
            for artifact in ("server", "smartdocs-admin", "smartdocs_test_server",
                             "smartdocs_tests"):
                if "FRESH" not in (repo / "bin" / artifact).read_text(
                        encoding="utf-8"):
                    problems.append(f"stale executable accepted: {artifact}")
            if (repo / "build-jobs").read_text(encoding="utf-8") != "-j1\n":
                problems.append("runner did not default to one build job")
            if "bin/smartdocs_tests" not in (repo / "build-arguments").read_text(
                    encoding="utf-8"):
                problems.append("runner did not build the C++ suite binary")
            evidence = json.loads((repo / "docs/evidence/m1/latest/results.json")
                                  .read_text(encoding="utf-8"))
            if not evidence["received_build_binding"]:
                problems.append("evidence driver did not receive build checkout metadata")
            if not evidence["received_supporting_suites"]:
                problems.append("evidence driver did not receive supporting suite outputs")
            self.assertEqual(problems, [])

    def test_runner_rejects_unsafe_build_parallelism_before_work(self):
        with tempfile.TemporaryDirectory(prefix="m1-runner-resource-test.") as root:
            repo = pathlib.Path(root)
            (repo / "test/e2e").mkdir(parents=True)
            runner = repo / "test/e2e/run_m1.sh"
            shutil.copy2(pathlib.Path(__file__).with_name("run_m1.sh"), runner)
            environment = os.environ.copy()
            environment["SMARTDOCS_BUILD_JOBS"] = "16"

            completed = subprocess.run(
                [str(runner)], cwd=repo, env=environment, text=True,
                capture_output=True, check=False)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unsafe_build_parallelism", completed.stderr)

    @staticmethod
    def _write_executable(path, content):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
