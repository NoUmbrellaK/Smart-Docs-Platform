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
        duplicate_task = "c" * 32
        duplicate_version = "d" * 32
        duplicate_job = "e" * 32
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
            {"files": 1, "upload_tasks": 2, "versions": 3,
             "target_versions": 2, "processing_jobs": 3,
             "completed_bindings": 2})
        self.assertFalse(harness.round_graph_is_singleton(counts, 1, 1))

    def test_half_publication_is_derived_from_database_and_http(self):
        derive = getattr(harness, "publication_observation", None)
        self.assertIsNotNone(
            derive,
            "constant false cannot expose uncommitted new bytes from the real file URL")
        seed = "1" * 64
        target = "2" * 64
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
            shutil.copy2(pathlib.Path(__file__).with_name("run_m1.sh"), runner)
            (repo / "test/fixtures/m1/plain.txt").write_text(
                "plain\n", encoding="utf-8")
            (repo / "test/fixtures/m1/sample.pdf").write_bytes(b"%PDF-1.4\n")
            self._write_executable(repo / "scripts/migrate.sh", "#!/bin/sh\nexit 0\n")
            for artifact in ("server", "smartdocs-admin", "smartdocs_test_server"):
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
                "repo=''\n"
                "clean=false\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    -C) repo=$2; shift 2 ;;\n"
                "    clean) clean=true; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if $clean; then\n"
                "  rm -f \"$repo/bin/server\" \"$repo/bin/smartdocs-admin\" "
                "\"$repo/bin/smartdocs_test_server\"\n"
                "else\n"
                "  mkdir -p \"$repo/bin\"\n"
                "  for artifact in server smartdocs-admin smartdocs_test_server; do\n"
                "    printf 'FRESH\\n' >\"$repo/bin/$artifact\"\n"
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
                fake_bin / "python3",
                "#!/usr/bin/python3\n"
                "import json, os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if args and args[0] == '-B': args.pop(0)\n"
                "if args and args[0] == '-':\n"
                "    os.execv('/usr/bin/python3', ['/usr/bin/python3', '-B'] + args)\n"
                "script = args.pop(0) if args else ''\n"
                "if script.endswith('m1_interrupt_test.py'):\n"
                "    required = {'--admin-bin', '--build-head', '--build-dirty'}\n"
                "    bound = required.issubset(args)\n"
                "    if bound:\n"
                "        bound = (args[args.index('--build-head') + 1] == '0' * 64 "
                "and args[args.index('--build-dirty') + 1] == 'false')\n"
                "    output = pathlib.Path(args[args.index('--output') + 1])\n"
                "    output.parent.mkdir(parents=True, exist_ok=True)\n"
                "    output.write_text(json.dumps({'received_build_binding': bound, "
                "'summary': {"
                "'http_scenarios_passed': 9, 'http_scenarios_total': 9, "
                "'interruption_rounds_passed': 20, "
                "'interruption_rounds_total': 20, 'http_assertions': 40, "
                "'interruption_assertions': 280}}), encoding='utf-8')\n")

            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
            environment["SMARTDOCS_BUILD_JOBS"] = "1"
            completed = subprocess.run(
                [str(runner)], cwd=repo, env=environment, text=True,
                capture_output=True, check=False)
            problems = []
            if completed.returncode != 0:
                problems.append(
                    f"runner exit {completed.returncode}: {completed.stderr}")
            for artifact in ("server", "smartdocs-admin", "smartdocs_test_server"):
                if (repo / "bin" / artifact).read_text(
                        encoding="utf-8") != "FRESH\n":
                    problems.append(f"stale executable accepted: {artifact}")
            evidence = json.loads((repo / "docs/evidence/m1/latest/results.json")
                                  .read_text(encoding="utf-8"))
            if not evidence["received_build_binding"]:
                problems.append("evidence driver did not receive build checkout metadata")
            self.assertEqual(problems, [])

    @staticmethod
    def _write_executable(path, content):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
