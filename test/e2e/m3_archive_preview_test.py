#!/usr/bin/env python3
"""Archive policy/preview through real C++ HTTP and isolated MySQL; no AI calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
import os
import secrets
import subprocess
import threading
import unittest

from m1_http_test import HttpClient, create_user, data, login, upload_file
from m2_worker_test import ROOT
from m3_task_list_test import TaskListE2E


class ArchivePreviewE2E(TaskListE2E):
    def base(self):
        return f'/api/v1/projects/{self.project["id"]}'

    def directory(self, name):
        return data(self.user.request("POST", self.base()+"/directories", {
            "parent_id": self.project["root_directory_id"], "name": name}), 201)

    def settings(self, target, revision=0, approved=True):
        return {"expected_revision": revision, "confirm_remote_ai": approved,
                "targets": [{"directory_id": target["id"], "name": target["name"]}]}

    def fixture(self):
        job = self.approved_file()
        target = self.directory("Approved destination")
        policy = data(self.user.request("PUT", self.base()+"/archive-policy", self.settings(target)))
        task = self.create_task(job)
        path = self.tasks_path()+"/"+task["id"]+"/archive-plan"
        body = {"expected_revision": 0, "policy_revision": policy["revision"], "items": [{
            "file_id": job["file_id"], "version_id": job["version_id"],
            "source_directory_id": self.project["root_directory_id"],
            "target_directory_id": target["id"], "reason": "Deployment material"}]}
        return job, target, task, path, body

    def file(self, job):
        items = data(self.user.request("GET", self.base()+"/files"))["items"]
        return next(item for item in items if item["id"] == job["file_id"])

    def member(self, role):
        name = "archive-"+secrets.token_hex(4)
        uid = create_user(str(ROOT/"bin/smartdocs-admin"), name, self.password)
        data(self.user.request("PUT", self.base()+"/members/"+uid, {"role": role}))
        client = HttpClient(self.server.port)
        login(client, name, self.password)
        return uid, client

    def count(self, statement):
        result = subprocess.run(["mysql", "--protocol=SOCKET", "--socket="+os.environ["SMARTDOCS_MYSQL_SOCKET"],
            "--user=root", "--batch", "--skip-column-names", "smart_docs_test", "--execute", statement],
            check=True, capture_output=True, text=True, timeout=10)
        return int(result.stdout.strip())

    def test_archive_policy_defaults_replay_revision_and_actual_rename_revokes(self):
        path = self.base()+"/archive-policy"
        self.assertEqual(data(self.user.request("GET", path)), {
            "revision": 0, "targets": [], "remote_ai_approved": False})
        target = self.directory("Approved")
        request = self.settings(target)
        saved = data(self.user.request("PUT", path, request))
        self.assertEqual(saved, {"revision": 1, "targets": request["targets"], "remote_ai_approved": True})
        self.assertEqual(data(self.user.request("PUT", path, request)), saved)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.policy.set' AND project_id='"+self.project["id"]+"'"), 1)
        self.assertEqual(self.user.request("PUT", path, self.settings(target, approved=False)).status, 409)
        # A no-op rename keeps approval; rename-away-and-back must not resurrect it.
        rename = self.base()+"/directories/"+target["id"]
        data(self.user.request("PATCH", rename, {"name": "Approved"}))
        self.assertEqual(data(self.user.request("GET", path)), saved)
        data(self.user.request("PATCH", rename, {"name": "Private renamed destination"}))
        changed = data(self.user.request("GET", path))
        self.assertEqual((changed["revision"], changed["remote_ai_approved"]), (2, False))
        data(self.user.request("PATCH", rename, {"name": "Approved"}))
        self.assertFalse(data(self.user.request("GET", path))["remote_ai_approved"])
        self.assertEqual(self.user.request("PUT", path, request).status, 409)

    def test_archive_preview_persists_replays_revises_without_moving_or_confirming(self):
        job, target, task, path, body = self.fixture()
        original = self.file(job)
        saved = data(self.user.request("PUT", path, body))
        self.assertEqual((saved["revision"], saved["state"], saved["executable"], saved["has_unresolved"]),
                         (1, "preview", False, False))
        self.assertEqual(saved["items"][0]["name"], "PRIVATE-FILENAME.txt")
        self.assertEqual(saved["items"][0]["target_name"], target["name"])
        self.assertEqual(data(self.user.request("PUT", path, body)), saved)
        different = copy.deepcopy(body)
        different["items"][0]["reason"] = "Different proposal"
        self.assertEqual(self.user.request("PUT", path, different).status, 409)
        different["expected_revision"] = 1
        different["items"][0]["target_directory_id"] = None
        revised = data(self.user.request("PUT", path, different))
        self.assertEqual((revised["id"], revised["revision"], revised["has_unresolved"], revised["executable"]),
                         (saved["id"], 2, True, False))
        self.assertEqual(self.file(job), original)
        self.assertEqual(data(self.user.request("GET", self.tasks_path()+"/"+task["id"]))["state"], "waiting_confirmation")
        self.server.stop()
        self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        self.assertEqual(data(self.user.request("GET", path)), revised)
        self.assertEqual(self.file(job), original)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.plan.save' AND object_id='"+saved["id"]+"'"), 2)
        data(self.user.request("POST", self.tasks_path()+"/"+task["id"]+"/cancel"))
        self.assertEqual(data(self.user.request("GET", path)), dict(revised, task_state="cancelled"))
        self.assertEqual(self.user.request("PUT", path, different).status, 409)
        data(self.user.request("PATCH", self.base()+"/files/"+job["file_id"], {
            "name": "Changed filename.txt", "directory_id": self.project["root_directory_id"]}))
        self.assertEqual(self.user.request("GET", path).status, 409)

    def test_archive_policy_rejects_foreign_directory_stale_name_and_ambiguous_input(self):
        path = self.base()+"/archive-policy"
        target = self.directory("Actual name")
        request = self.settings(target)
        bad = copy.deepcopy(request)
        bad["targets"][0]["name"] = "Wrong shown name"
        self.assertEqual(self.user.request("PUT", path, bad).status, 409)
        foreign = data(self.user.request("POST", "/api/v1/projects", {"name": "Foreign"}), 201)
        bad["targets"][0] = {"directory_id": foreign["root_directory_id"], "name": "/"}
        self.assertEqual(self.user.request("PUT", path, bad).status, 404)
        for bad in (dict(request, approved=True), dict(request, expected_revision=True),
                    dict(request, targets=request["targets"]*2), dict(request, confirm_remote_ai="true")):
            self.assertEqual(self.user.request("PUT", path, bad).status, 400)
        self.assertEqual(self.user.request("GET", path+"?user_id=x").status, 400)
        self.assertEqual(self.user.request("PUT", path, request, {"Origin": "https://foreign.invalid"}).status, 403)
        self.assertEqual(data(self.user.request("GET", path))["revision"], 0)

    def test_archive_preview_owner_role_project_and_same_origin(self):
        job, target, task, path, body = self.fixture()
        uid, reader = self.member("reader")
        own = self.create_task(job, reader)
        own_path = self.tasks_path()+"/"+own["id"]+"/archive-plan"
        self.assertEqual(reader.request("PUT", own_path, body).status, 403)
        self.assertEqual(reader.request("PUT", self.base()+"/archive-policy", self.settings(target, 1)).status, 403)
        data(self.user.request("PUT", self.base()+"/members/"+uid, {"role": "editor"}))
        data(reader.request("PUT", own_path, body))
        self.assertEqual(reader.request("GET", path).status, 404)
        self.assertEqual(self.user.request("GET", own_path).status, 404)
        self.assertEqual(self.user.request("PUT", path, body, {"Origin": "https://foreign.invalid"}).status, 403)
        self.assertEqual(HttpClient(self.server.port).request("GET", path).status, 401)
        self.sql("DELETE FROM project_members WHERE project_id='"+self.project["id"]+"' AND user_id='"+uid+"'")
        self.assertEqual(reader.request("GET", own_path).status, 404)

    def test_archive_preview_strict_parameters_unapproved_and_out_of_scope_targets(self):
        job, target, task, path, body = self.fixture()
        for extra in ({"approved": True}, {"operation_id": "0"*32}, {"user_id": "0"*32}):
            self.assertEqual(self.user.request("PUT", path, dict(body, **extra)).status, 400)
        for revision in (True, -1, 1.5, "0", 4294967295):
            self.assertEqual(self.user.request("PUT", path, dict(body, expected_revision=revision)).status, 400)
        self.assertEqual(self.user.request("GET", path+"?approved=true").status, 400)
        self.assertEqual(self.user.request("GET", path, {}).status, 400)
        for reason in ("", "   ", "a"*1001, "bad\x00reason"):
            invalid = copy.deepcopy(body)
            invalid["items"][0]["reason"] = reason
            self.assertEqual(self.user.request("PUT", path, invalid).status, 400)
        for items in ([], body["items"]*2, body["items"]*101):
            self.assertEqual(self.user.request("PUT", path, dict(body, items=items)).status, 400)
        other = self.directory("NOT APPROVED")
        invalid = copy.deepcopy(body)
        invalid["items"][0]["target_directory_id"] = other["id"]
        denied = self.user.request("PUT", path, invalid)
        self.assertEqual(denied.status, 409)
        self.assertNotIn(b"NOT APPROVED", denied.body)
        for key in ("file_id", "version_id", "source_directory_id"):
            invalid = copy.deepcopy(body)
            invalid["items"][0][key] = "0"*32
            self.assertIn(self.user.request("PUT", path, invalid).status, (404, 409))
        data(self.user.request("PUT", self.base()+"/archive-policy", self.settings(target, 1, False)))
        body["policy_revision"] = 2
        self.assertEqual(self.user.request("PUT", path, body).status, 409)
        self.assertEqual(self.user.request("GET", path).status, 404)

    def test_archive_preview_rechecks_whole_scope_policy_location_and_cancel(self):
        job, target, task, path, body = self.fixture()
        data(self.user.request("PUT", path, body))
        data(self.user.request("PATCH", self.base()+"/files/"+job["file_id"], {
            "name": "PRIVATE-FILENAME.txt", "directory_id": target["id"]}))
        self.assertEqual(self.user.request("GET", path).status, 409)
        self.assertEqual(self.user.request("PUT", path, body).status, 409)
        data(self.user.request("POST", self.tasks_path()+"/"+task["id"]+"/cancel"))
        self.assertEqual(self.user.request("PUT", path, dict(body, expected_revision=1)).status, 409)
        # A non-proposed file still participated in the task's allowed context.
        second = self.upload("other.txt", b"other context", "text/plain")
        self.policy(second, True)
        whole = data(self.user.request("POST", self.tasks_path(), {"question": "archive", "confirm_remote_ai": True,
            "versions": [{"file_id": j["file_id"], "version_id": j["version_id"]} for j in (job, second)]}), 201)
        self.policy(second, False)
        self.assertEqual(self.user.request("PUT", self.tasks_path()+"/"+whole["id"]+"/archive-plan", body).status, 409)

    def test_archive_preview_concurrent_cas_publishes_only_one_proposal(self):
        job, target, task, path, body = self.fixture()
        barrier = threading.Barrier(2)
        cookie = self.user._cookie_header()
        def save(reason):
            request = copy.deepcopy(body)
            request["items"][0]["reason"] = reason
            barrier.wait(timeout=5)
            return HttpClient(self.server.port).request("PUT", path, request, {"Cookie": cookie})
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(save, ("Proposal one", "Proposal two")))
        self.assertEqual(sorted(response.status for response in responses), [200, 409])
        winner = next(data(response) for response in responses if response.status == 200)
        self.assertEqual(data(self.user.request("GET", path)), winner)
        self.assertEqual(self.count("SELECT COUNT(*) FROM archive_plans WHERE task_id='"+task["id"]+"'"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_records WHERE action='archive.plan.save' AND object_id='"+winner["id"]+"'"), 1)
        self.assertEqual(self.file(job)["directory_id"], self.project["root_directory_id"])

    def test_archive_preview_rejects_same_name_conflict_and_changed_target_metadata(self):
        job, target, task, path, body = self.fixture()
        _, blocker = upload_file(self.user, self.project["id"], target["id"], "private-filename.TXT", b"block", "text/plain")
        self.jobs.append(blocker)
        self.assertEqual(self.user.request("PUT", path, body).status, 409)
        data(self.user.request("POST", self.job_path(blocker)+"/cancel"))
        self.assertEqual(self.user.request("DELETE", self.base()+"/files/"+blocker["file_id"]).status, 204)
        self.jobs.remove(blocker)  # Deleted files intentionally make job status inaccessible.
        data(self.user.request("PUT", path, body))
        data(self.user.request("PATCH", self.base()+"/directories/"+target["id"], {"name": "INTERNAL NEW NAME"}))
        result = self.user.request("GET", path)
        self.assertEqual(result.status, 409)
        self.assertNotIn(b"INTERNAL NEW NAME", result.body)
        self.assertEqual(self.file(job)["directory_id"], self.project["root_directory_id"])

    def test_archive_preview_rejects_batch_collation_collision_and_new_version(self):
        job, target, task, path, body = self.fixture()
        source2 = self.directory("Second source")
        _, second = upload_file(self.user, self.project["id"], source2["id"], "private-filename.TXT", b"second", "text/plain")
        self.jobs.append(second)
        self.policy(second, True)
        whole = data(self.user.request("POST", self.tasks_path(), {"question": "archive", "confirm_remote_ai": True,
            "versions": [{"file_id": j["file_id"], "version_id": j["version_id"]} for j in (job, second)]}), 201)
        batch_path = self.tasks_path()+"/"+whole["id"]+"/archive-plan"
        batch = copy.deepcopy(body)
        batch["items"].append(dict(batch["items"][0], file_id=second["file_id"], version_id=second["version_id"],
                                   source_directory_id=source2["id"]))
        self.assertEqual(self.user.request("PUT", batch_path, batch).status, 409)
        self.assertEqual(self.file(second)["directory_id"], source2["id"])
        data(self.user.request("PUT", path, body))
        # Simulate the exact current-version switch by creating a real new upload.
        from m1_http_test import create_upload, upload_parts, complete
        content = b"new version"
        upload = create_upload(self.user, self.project["id"], self.project["root_directory_id"],
            "PRIVATE-FILENAME.txt", content, "text/plain", job["file_id"], job["version_id"])
        upload_parts(self.user, self.project["id"], upload, content)
        self.jobs.append(data(complete(self.user, self.project["id"], upload["task_id"])))
        self.assertEqual(self.user.request("GET", path).status, 409)


if __name__ == "__main__":
    suite = unittest.TestSuite(ArchivePreviewE2E(name) for name in dir(ArchivePreviewE2E) if name.startswith("test_archive_"))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
