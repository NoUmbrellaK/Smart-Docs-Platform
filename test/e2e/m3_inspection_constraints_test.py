#!/usr/bin/env python3
"""Authoritative task inspection constraints through real HTTP and isolated SQL."""
import json
import os
import subprocess
import unittest

from m1_http_test import HttpClient, data, login
from m3_handover_context_test import HandoverContextE2E
from smartdocs import worker


class InspectionConstraintsE2E(HandoverContextE2E):
    constraints = {"mode": "metadata", "max_page": 1, "page_size": 10}

    def create_constrained(self, job, constraints=None, actor=None, **extra):
        return data((actor or self.user).request("POST", self.tasks_path(), {
            "question": "Inspect only metadata", "confirm_remote_ai": True,
            "versions": [{"file_id": job["file_id"], "version_id": job["version_id"]}],
            "inspection_constraints": constraints or self.constraints, **extra}), 201)

    def test_inspection_constraints_create_get_list_start_persist(self):
        job = self.approved_file()
        constraints = self.constraints
        created = self.create_constrained(job)
        path = self.tasks_path() + "/" + created["id"]
        self.addCleanup(lambda: self.user.request("POST", path + "/cancel"))

        self.assertEqual(created["inspection_constraints"], constraints)
        self.assertEqual(data(self.user.request("GET", path))["inspection_constraints"], constraints)
        listed = data(self.user.request("GET", self.tasks_path()))["items"]
        self.assertEqual(next(item for item in listed if item["id"] == created["id"])["inspection_constraints"],
                         constraints)

        query = subprocess.run([
            "mysql", "--protocol=SOCKET", "--socket=" + os.environ["SMARTDOCS_MYSQL_SOCKET"],
            "--user=root", "--batch", "--skip-column-names", "smart_docs_test", "--execute",
            "SELECT inspection_constraints FROM agent_tasks WHERE id='" + created["id"] + "'"],
            check=True, capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(query.stdout), constraints)
        self.assertEqual(data(self.user.request("POST", path + "/run/start"))["inspection_constraints"],
                         constraints)

    def test_inspection_constraints_reject_malformed_create_values(self):
        job = self.approved_file()
        invalid = [
            None, True, 1, "metadata", [], {},
            {"mode": "keyword", "max_page": 1, "page_size": 10},
            {"mode": "metadata", "max_page": 1, "page_size": 10, "extra": True},
            {"mode": "metadata", "page_size": 10},
            {"mode": "metadata", "max_page": 1},
            {"mode": "metadata", "max_page": 0, "page_size": 10},
            {"mode": "metadata", "max_page": -1, "page_size": 10},
            {"mode": "metadata", "max_page": True, "page_size": 10},
            {"mode": "metadata", "max_page": 1.5, "page_size": 10},
            {"mode": "metadata", "max_page": 1001, "page_size": 10},
            {"mode": "metadata", "max_page": 1, "page_size": 0},
            {"mode": "metadata", "max_page": 1, "page_size": True},
            {"mode": "metadata", "max_page": 1, "page_size": 1.5},
            {"mode": "metadata", "max_page": 1, "page_size": 11},
        ]
        for constraints in invalid:
            with self.subTest(constraints=constraints):
                response = self.user.request("POST", self.tasks_path(), {
                    "question": "Inspect only metadata", "confirm_remote_ai": True,
                    "versions": [{"file_id": job["file_id"], "version_id": job["version_id"]}],
                    "inspection_constraints": constraints})
                self.assertEqual(response.status, 400)
        self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tasks WHERE project_id='" +
                                    self.project["id"] + "'"), 0)
        created = self.create_constrained(job)
        malformed = [None, True, {"mode": "metadata", "max_page": 1},
                     {"mode": "metadata", "max_page": 0, "page_size": 10},
                     {"mode": "metadata", "max_page": 1.5, "page_size": 10},
                     {"mode": "metadata", "max_page": 1001, "page_size": 10},
                     {"mode": "metadata", "max_page": 1, "page_size": 11},
                     {"mode": "metadata", "max_page": 1, "page_size": 10, "extra": 1}]
        for value in malformed:
            with self.subTest(database_value=value):
                encoded = json.dumps(value, separators=(",", ":"))
                with self.assertRaises(subprocess.CalledProcessError):
                    self.sql("UPDATE agent_tasks SET inspection_constraints='" + encoded +
                             "' WHERE id='" + created["id"] + "'")
        self.assertEqual(data(self.user.request("GET", self.tasks_path() + "/" + created["id"]))[
            "inspection_constraints"], self.constraints)

    def test_inspection_constraints_enforce_search_and_read_with_safe_audit(self):
        marker = b"SAMPLE-TEXT-NEVER-LEAK"
        job = self.upload("sample.txt", marker, "text/plain")
        self.policy(job, True)
        self.assertEqual(worker.run_once(self.client), "completed")
        task = self.create_constrained(job)
        path = self.tasks_path() + "/" + task["id"]

        self.assertEqual(self.user.request("GET", self.version_path(job) + "/document").status, 200)
        self.assertEqual(self.user.request("GET", self.version_path(job) + "/content").body, marker)
        ordinary = data(self.user.request("POST", "/api/v1/projects/" + self.project["id"] + "/search", {
            "mode": "keyword", "query": "SAMPLE"}))
        chunk_id = ordinary["items"][0]["chunk"]["chunk_id"]
        self.assertEqual(data(self.user.request("GET", self.version_path(job) + "/chunks/" + chunk_id))[
            "chunk"]["text"].encode(), marker)

        allowed = self.user.request("POST", path + "/tools/search", {
            "mode": "metadata", "query": "", "page": 1, "page_size": 10})
        self.assertEqual(allowed.status, 200)
        denied = [
            self.user.request("POST", path + "/tools/search", {
                "mode": "metadata", "query": "", "page": 2, "page_size": 10}),
            self.user.request("POST", path + "/tools/search", {
                "mode": "keyword", "query": "SAMPLE", "page": 1, "page_size": 10}),
            self.user.request("POST", path + "/tools/search", {
                "mode": "hybrid", "query": "SAMPLE", "page": 1, "page_size": 10}),
            self.user.request("POST", path + "/tools/read", {
                "file_id": job["file_id"], "version_id": job["version_id"]}),
            self.user.request("POST", path + "/tools/read", {
                "file_id": job["file_id"], "version_id": job["version_id"], "chunk_id": "0" * 64}),
            self.user.request("POST", path + "/tools/read", {
                "file_id": job["file_id"], "version_id": job["version_id"], "range": {}}),
        ]
        for response in denied:
            self.assertEqual(response.status, 403)
            self.assertEqual(response.json()["error"]["code"], "task_inspection_restricted")
            self.assertNotIn(marker, response.body)
            self.assertNotIn("data", response.json())
        rows = data(self.user.request("GET", path + "/tool-calls"))["items"]
        self.assertEqual([row["status"] for row in rows], ["success"] + ["access_denied"] * 6)
        self.assertNotIn(marker, self.user.request("GET", path + "/tool-calls").body)

    def test_inspection_constraints_allow_exact_bounds_and_reject_defaults_above_cap(self):
        job = self.approved_file()
        task = self.create_constrained(job, {"mode": "metadata", "max_page": 2, "page_size": 2})
        path = self.tasks_path() + "/" + task["id"] + "/tools/search"
        for body in ({"mode": "metadata", "query": "", "page": 1, "page_size": 2},
                     {"mode": "metadata", "query": "", "page": 2, "page_size": 2},
                     {"mode": "metadata", "query": "", "page_size": 2}):
            self.assertEqual(self.user.request("POST", path, body).status, 200)
        for body in ({"mode": "metadata", "query": ""},
                     {"mode": "metadata", "query": "", "page": 3, "page_size": 2},
                     {"mode": "metadata", "query": "", "page": 1, "page_size": 3}):
            response = self.user.request("POST", path, body)
            self.assertEqual(response.status, 403)
            self.assertEqual(response.json()["error"]["code"], "task_inspection_restricted")

    def test_inspection_constraints_preserve_legacy_owner_cancel_and_restart_contracts(self):
        job = self.approved_file()
        ordinary = self.create_task(job)
        ordinary_path = self.tasks_path() + "/" + ordinary["id"]
        self.assertNotIn("inspection_constraints", ordinary)
        self.assertNotIn("inspection_constraints", data(self.user.request("GET", ordinary_path)))
        self.assertNotIn("inspection_constraints", data(self.user.request("POST", ordinary_path + "/run/start")))
        self.assertEqual(self.user.request("POST", ordinary_path + "/tools/search", {
            "mode": "keyword", "query": "SOURCE"}).status, 200)
        self.assertEqual(self.user.request("POST", ordinary_path + "/tools/read", {
            "file_id": job["file_id"], "version_id": job["version_id"], "range": {}}).status, 200)

        _, reader = self.member("reader")
        private = self.create_constrained(job, actor=reader)
        private_path = self.tasks_path() + "/" + private["id"]
        self.assertEqual(self.user.request("GET", private_path).status, 404)
        self.assertEqual(self.user.request("POST", private_path + "/run/start").status, 404)
        self.assertEqual(data(reader.request("GET", private_path))["inspection_constraints"], self.constraints)
        data(reader.request("POST", private_path + "/cancel"))
        self.assertEqual(reader.request("POST", private_path + "/tools/search", {
            "mode": "metadata", "query": ""}).status, 409)

        persistent = self.create_constrained(job)
        persistent_path = self.tasks_path() + "/" + persistent["id"]
        self.server.stop()
        self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        self.assertEqual(data(self.user.request("GET", persistent_path))["inspection_constraints"], self.constraints)
        self.assertEqual(data(self.user.request("POST", persistent_path + "/run/start"))["inspection_constraints"],
                         self.constraints)

    def test_inspection_constraints_do_not_change_report_or_archive_tools(self):
        job = self.approved_file()
        self.checklist()
        report = self.create_constrained(job, handover_checklist_revision=1)
        report_path = self.tasks_path() + "/" + report["id"]
        started = data(self.user.request("POST", report_path + "/run/start"))
        self.assertEqual(started["inspection_constraints"], self.constraints)
        data(self.user.request("POST", report_path + "/run/check", {
            "tool_request_ids": [], "handover_checklist_revision": 1}))
        receipt = data(self.user.request("PUT", report_path + "/handover-report", {
            "operation_id": started["report_operation_id"], "report": {
                "summary": "No document body inspected.", "limitations": "Metadata-only task.",
                "recommendations": "Review evidence manually.", "items": [{
                    "key": "deployment", "status": "uncertain", "notes": "No body was inspected.",
                    "citations": []}]}}))
        self.assertEqual(receipt["version"], 1)

        target = self.directory("Approved destination")
        data(self.user.request("PUT", self.base() + "/archive-policy", self.settings(target)))
        archive = self.create_constrained(job, archive_policy_revision=1)
        archive_path = self.tasks_path() + "/" + archive["id"] + "/archive-plan"
        plan = data(self.user.request("PUT", archive_path, {
            "expected_revision": 0, "policy_revision": 1, "items": [{
                "file_id": job["file_id"], "version_id": job["version_id"],
                "source_directory_id": self.project["root_directory_id"],
                "target_directory_id": target["id"], "reason": "Metadata-only proposal"}]}))
        self.assertEqual((plan["state"], plan["task_state"]), ("preview", "waiting_confirmation"))


if __name__ == "__main__":
    suite = unittest.TestSuite(InspectionConstraintsE2E(name) for name in InspectionConstraintsE2E.__dict__
                               if name.startswith("test_inspection_constraints_"))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
