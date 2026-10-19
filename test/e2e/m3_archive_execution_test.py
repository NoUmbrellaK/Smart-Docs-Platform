#!/usr/bin/env python3
"""Real same-origin confirmation and atomic archive execution; disposable DB only."""
import copy
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import subprocess
import threading
import unittest

from m1_http_test import HttpClient, create_upload, upload_parts, complete, data, login
from m3_archive_preview_test import ArchivePreviewE2E


class ArchiveExecutionE2E(ArchivePreviewE2E):
    def prepared(self, two=False):
        job, target, task, path, body = self.fixture()
        jobs = [job]
        if two:
            second = self.upload("second.txt", b"second sample", "text/plain")
            self.policy(second, True)
            jobs.append(second)
            task = data(self.user.request("POST", self.tasks_path(), {"question": "Archive samples", "confirm_remote_ai": True,
                "versions": [{"file_id": j["file_id"], "version_id": j["version_id"]} for j in jobs]}), 201)
            path = self.tasks_path()+"/"+task["id"]+"/archive-plan"
            body["items"].append(dict(body["items"][0], file_id=second["file_id"], version_id=second["version_id"]))
        plan = data(self.user.request("PUT", path, body))
        return jobs, target, task, path, body, plan

    def decision(self, plan, decision="confirm"):
        return {"plan_id": plan["id"], "revision": plan["revision"],
                "content_sha256": plan.get("content_sha256", "0"*64), "decision": decision}

    def confirm(self, path, plan):
        return data(self.user.request("POST", path+"/decision", self.decision(plan)))

    def execution(self, plan):
        return {"plan_id": plan["id"], "revision": plan["revision"], "operation_id": plan.get("operation_id") or "0"*32}

    def test_execution_waiting_releases_slot_and_never_accepts_model_approval(self):
        jobs, target, task, path, body, plan = self.prepared()
        self.assertEqual(data(self.user.request("GET", self.tasks_path()+"/"+task["id"]))["state"], "waiting_confirmation")
        self.assertRegex(plan["content_sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(plan["executable"])
        self.assertIsNone(plan["operation_id"])
        self.assertEqual(self.user.request("POST", path+"/execute", self.execution(plan)).status, 409)
        self.assertEqual(self.user.request("POST", path+"/execute", dict(self.execution(plan), approved=True)).status, 400)
        self.assertEqual(self.user.request("POST", self.tasks_path()+"/"+task["id"]+"/run/start").status, 409)
        self.assertEqual(self.file(jobs[0])["directory_id"], self.project["root_directory_id"])

    def test_execution_confirmation_is_separate_and_batch_commit_replays_once(self):
        jobs, target, task, path, body, plan = self.prepared(two=True)
        confirmed = self.confirm(path, plan)
        self.assertEqual((confirmed["state"], confirmed["task_state"]), ("confirmed", "waiting_execution"))
        self.assertTrue(confirmed["executable"])
        self.assertRegex(confirmed["operation_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.confirm(path, plan), confirmed)
        for job in jobs:
            self.assertEqual(self.file(job)["directory_id"], self.project["root_directory_id"])
        command = self.execution(confirmed)
        receipt = data(self.user.request("POST", path+"/execute", command))
        self.assertEqual((receipt["status"], receipt["moved_count"]), ("executed", 2))
        self.assertEqual({x["file_id"] for x in receipt["items"]}, {j["file_id"] for j in jobs})
        self.assertEqual(data(self.user.request("POST", path+"/execute", command)), receipt)
        for job in jobs:
            current = self.file(job)
            self.assertEqual((current["directory_id"], current["current_version_id"]), (target["id"], job["version_id"]))
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan["id"]+"'"), 1)
        self.assertEqual(data(self.user.request("GET", path))["state"], "executed")
        outcome = data(self.user.request("GET", self.tasks_path()+"/"+task["id"]+"/run/result"))
        self.assertEqual((outcome["kind"], outcome["model_calls"], outcome["result"]), ("archive_operation", 0, None))
        self.assertEqual(outcome["archive_operation"]["operation_id"], receipt["operation_id"])
        self.assertEqual(self.user.request("PUT", path, dict(body, expected_revision=1)).status, 409)

    def test_execution_reject_old_digest_revision_and_unresolved_targets(self):
        jobs, target, task, path, body, plan = self.prepared()
        wrong = dict(self.decision(plan), content_sha256="0"*64)
        self.assertEqual(self.user.request("POST", path+"/decision", wrong).status, 409)
        rejected = data(self.user.request("POST", path+"/decision", self.decision(plan, "reject")))
        self.assertEqual(rejected["state"], "rejected")
        self.assertIsNone(rejected["operation_id"])
        self.assertEqual(self.user.request("POST", path+"/decision", self.decision(plan)).status, 409)
        self.assertEqual(self.user.request("POST", path+"/execute", self.execution(rejected)).status, 409)
        revised = copy.deepcopy(body)
        revised["expected_revision"] = 1
        revised["items"][0]["target_directory_id"] = None
        pending = data(self.user.request("PUT", path, revised))
        self.assertEqual(self.user.request("POST", path+"/decision", self.decision(pending)).status, 409)
        revised["expected_revision"] = 2
        revised["items"][0]["target_directory_id"] = target["id"]
        final = data(self.user.request("PUT", path, revised))
        self.assertEqual(self.user.request("POST", path+"/decision", self.decision(plan)).status, 409)
        confirmed = self.confirm(path, final)
        revised["expected_revision"] = 3
        revised["items"][0]["reason"] = "Explicit new revision"
        changed = data(self.user.request("PUT", path, revised))
        self.assertIsNone(changed["operation_id"])
        self.assertEqual(self.user.request("POST", path+"/execute", self.execution(confirmed)).status, 409)
        self.assertEqual(self.file(jobs[0])["directory_id"], self.project["root_directory_id"])

    def test_execution_checks_owner_role_origin_and_cancel_before_commit(self):
        jobs, target, task, path, body, plan = self.prepared()
        uid, editor = self.member("editor")
        self.assertEqual(editor.request("POST", path+"/decision", self.decision(plan)).status, 404)
        self.assertEqual(self.user.request("POST", path+"/decision", self.decision(plan), {"Origin": "https://foreign.invalid"}).status, 403)
        own = self.create_task(jobs[0], editor)
        own_path = self.tasks_path()+"/"+own["id"]+"/archive-plan"
        own_plan = data(editor.request("PUT", own_path, body))
        confirmed = data(editor.request("POST", own_path+"/decision", self.decision(own_plan)))
        data(self.user.request("PUT", self.base()+"/members/"+uid, {"role": "reader"}))
        self.assertEqual(editor.request("POST", own_path+"/execute", self.execution(confirmed)).status, 403)
        data(self.user.request("PUT", self.base()+"/members/"+uid, {"role": "editor"}))
        cancelled = data(editor.request("POST", self.tasks_path()+"/"+own["id"]+"/cancel"))
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(editor.request("POST", own_path+"/execute", self.execution(confirmed)).status, 409)
        self.assertFalse(data(editor.request("GET", own_path))["executable"])
        self.assertEqual(self.file(jobs[0])["directory_id"], self.project["root_directory_id"])

    def test_execution_rechecks_location_and_target_policy_after_confirmation(self):
        jobs, target, task, path, body, plan = self.prepared(two=True)
        confirmed = self.confirm(path, plan)
        other = self.directory("Elsewhere")
        data(self.user.request("PATCH", self.base()+"/files/"+jobs[0]["file_id"], {
            "name": "PRIVATE-FILENAME.txt", "directory_id": other["id"]}))
        self.assertEqual(self.user.request("POST", path+"/execute", self.execution(confirmed)).status, 409)
        self.assertEqual(self.file(jobs[1])["directory_id"], self.project["root_directory_id"])
        data(self.user.request("PATCH", self.base()+"/files/"+jobs[0]["file_id"], {
            "name": "PRIVATE-FILENAME.txt", "directory_id": self.project["root_directory_id"]}))
        data(self.user.request("PATCH", self.base()+"/directories/"+target["id"], {"name": "PRIVATE RENAMED TARGET"}))
        response = self.user.request("POST", path+"/execute", self.execution(confirmed))
        self.assertEqual(response.status, 409)
        self.assertNotIn(b"PRIVATE RENAMED TARGET", response.body)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan["id"]+"'"), 0)

    def test_execution_whole_scope_revocation_and_new_version_block_moves(self):
        jobs, target, task, path, body, plan = self.prepared(two=True)
        reduced = copy.deepcopy(body)
        reduced["expected_revision"] = 1
        reduced["items"] = reduced["items"][:1]
        current = data(self.user.request("PUT", path, reduced))
        confirmed = self.confirm(path, current)
        self.policy(jobs[1], False)
        self.assertEqual(self.user.request("POST", path+"/execute", self.execution(confirmed)).status, 409)
        self.policy(jobs[1], True)
        content = b"new version"
        upload = create_upload(self.user, self.project["id"], self.project["root_directory_id"], "second.txt",
            content, "text/plain", jobs[1]["file_id"], jobs[1]["version_id"])
        upload_parts(self.user, self.project["id"], upload, content)
        self.jobs.append(data(complete(self.user, self.project["id"], upload["task_id"])))
        self.assertEqual(self.user.request("POST", path+"/execute", self.execution(confirmed)).status, 409)
        self.assertEqual(self.file(jobs[0])["directory_id"], self.project["root_directory_id"])

    def test_execution_database_failure_after_first_move_rolls_back_all_business_state(self):
        jobs, target, task, path, body, plan = self.prepared(two=True)
        confirmed = self.confirm(path, plan)
        first, second = jobs
        # Trigger fires only when first file has already moved in the same transaction.
        self.sql("DELIMITER //\nCREATE TRIGGER archive_test_abort BEFORE UPDATE ON files FOR EACH ROW BEGIN "
            "IF NEW.id='"+second["file_id"]+"' AND NEW.directory_id='"+target["id"]+"' AND "
            "(SELECT directory_id FROM files WHERE id='"+first["file_id"]+"')='"+target["id"]+"' THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='isolated archive rollback fixture'; END IF; END//\nDELIMITER ;")
        try:
            response = self.user.request("POST", path+"/execute", self.execution(confirmed))
            self.assertEqual(response.status, 503)
            for job in jobs:
                self.assertEqual(self.file(job)["directory_id"], self.project["root_directory_id"])
            self.assertEqual(self.count("SELECT COUNT(*) FROM archive_decisions WHERE task_id='"+task["id"]+"' AND result_json IS NOT NULL"), 0)
            self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan["id"]+"'"), 0)
            self.assertEqual(self.user.request("GET", path+"/result").status, 404)
        finally:
            self.sql("DROP TRIGGER archive_test_abort")
        receipt = data(self.user.request("POST", path+"/execute", self.execution(confirmed)))
        self.assertEqual(receipt["moved_count"], 2)

    def test_execution_lost_body_restart_and_later_move_do_not_repeat_operation(self):
        jobs, target, task, path, body, plan = self.prepared()
        confirmed = self.confirm(path, plan)
        command = self.execution(confirmed)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        connection.request("POST", path+"/execute", json.dumps(command), {
            "Cookie": self.user._cookie_header(), "Origin": f"http://127.0.0.1:{self.server.port}", "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        connection.close()  # Intentionally never consume the successful JSON body.
        self.server.stop()
        self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        receipt = data(self.user.request("GET", path+"/result"))
        self.assertEqual(set(receipt), {"operation_id", "plan_id", "revision", "status", "moved_count", "audit_id"})
        data(self.user.request("PATCH", self.base()+"/files/"+jobs[0]["file_id"], {
            "name": "PRIVATE-FILENAME.txt", "directory_id": self.project["root_directory_id"]}))
        replay = data(self.user.request("POST", path+"/execute", command))
        self.assertEqual(replay["operation_id"], receipt["operation_id"])
        self.assertEqual(self.file(jobs[0])["directory_id"], self.project["root_directory_id"])
        self.assertEqual(data(self.user.request("POST", self.tasks_path()+"/"+task["id"]+"/cancel"))["state"], "completed")
        self.policy(jobs[0], False)
        self.assertEqual(data(self.user.request("GET", path+"/result")), receipt)
        self.assertEqual(self.user.request("POST", path+"/execute", command).status, 409)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan["id"]+"'"), 1)

    def test_execution_concurrent_identical_requests_have_one_commit_and_receipt(self):
        jobs, target, task, path, body, plan = self.prepared(two=True)
        confirmed = self.confirm(path, plan)
        command = self.execution(confirmed)
        barrier = threading.Barrier(2)
        cookie = self.user._cookie_header()
        def execute(_):
            barrier.wait(timeout=5)
            return HttpClient(self.server.port).request("POST", path+"/execute", command, {"Cookie": cookie})
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(execute, range(2)))
        self.assertEqual([r.status for r in responses], [200, 200])
        self.assertEqual(data(responses[0]), data(responses[1]))
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.execute' AND object_id='"+plan["id"]+"'"), 1)

    def test_execution_expired_model_run_cannot_publish_new_plan(self):
        job, target, task, path, body = self.fixture()
        data(self.user.request("POST", self.tasks_path()+"/"+task["id"]+"/run/start"))
        self.sql("UPDATE agent_runs SET started_at=UTC_TIMESTAMP(6)-INTERVAL 181 SECOND WHERE task_id='"+task["id"]+"'")
        self.assertEqual(self.user.request("PUT", path, body).status, 409)
        self.assertEqual(self.user.request("GET", path).status, 404)
        data(self.user.request("POST", self.tasks_path()+"/"+task["id"]+"/cancel"))

    def test_execution_waiting_releases_running_slot_and_preserves_budget_on_restart(self):
        job, target, task, path, body = self.fixture()
        run = self.tasks_path()+"/"+task["id"]+"/run"
        data(self.user.request("POST", run+"/start"))
        data(self.user.request("POST", run+"/check", {"tool_request_ids": []}))
        plan = data(self.user.request("PUT", path, body))
        others = [self.create_task(job) for _ in range(3)]
        try:
            for other in others[:2]:
                data(self.user.request("POST", self.tasks_path()+"/"+other["id"]+"/run/start"))
            self.assertEqual(self.user.request("POST", self.tasks_path()+"/"+others[2]["id"]+"/run/start").status, 429)
        finally:
            for other in others:
                data(self.user.request("POST", self.tasks_path()+"/"+other["id"]+"/cancel"))
        confirmed = self.confirm(path, plan)
        self.server.stop()
        self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        self.assertEqual(data(self.user.request("GET", path)), confirmed)
        self.assertEqual(self.count("SELECT model_calls FROM agent_runs WHERE task_id='"+task["id"]+"'"), 1)
        self.assertEqual(self.user.request("POST", run+"/start").status, 409)
        self.assertEqual(self.user.request("POST", run+"/check", {"tool_request_ids": []}).status, 409)
        data(self.user.request("POST", path+"/execute", self.execution(confirmed)))

    def test_execution_new_destination_conflict_after_confirmation_blocks_whole_batch(self):
        jobs, target, task, path, body, plan = self.prepared(two=True)
        confirmed = self.confirm(path, plan)
        occupant = self.upload("new-occupant.txt", b"occupied", "text/plain")
        data(self.user.request("PATCH", self.base()+"/files/"+occupant["file_id"], {
            "name": "second.txt", "directory_id": target["id"]}))
        self.assertEqual(self.user.request("POST", path+"/execute", self.execution(confirmed)).status, 409)
        for job in jobs:
            self.assertEqual(self.file(job)["directory_id"], self.project["root_directory_id"])

    def test_execution_schema_rejects_cross_task_plan_decision(self):
        jobs, target, task, path, body, plan = self.prepared()
        other = self.create_task(jobs[0])
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.sql("INSERT INTO archive_decisions(task_id,revision,plan_id,content_sha256,actor_user_id,decision,operation_id) "
                "SELECT '"+other["id"]+"',1,'"+plan["id"]+"','"+plan["content_sha256"]+"',owner_user_id,'confirm',REPEAT('f',32) "
                "FROM agent_tasks WHERE id='"+other["id"]+"'")
        self.assertIn("foreign key constraint fails", caught.exception.stderr)


if __name__ == "__main__":
    suite = unittest.TestSuite(ArchiveExecutionE2E(name) for name in dir(ArchiveExecutionE2E)
        if name.startswith(("test_archive_", "test_execution_")))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
