#!/usr/bin/env python3
"""Project checklist contracts through real C++/isolated MySQL; no model calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
import unittest

from m1_http_test import HttpClient, data, login
from m3_archive_preview_test import ArchivePreviewE2E


class HandoverChecklistE2E(ArchivePreviewE2E):
    def path(self):
        return self.base()+"/handover-checklist"

    def request_body(self, revision=0, approved=False):
        return {"expected_revision": revision, "confirm_remote_ai": approved, "items": [
            {"key": "deployment", "title": "部署说明", "description": "配置、步骤与回滚", "required": True},
            {"key": "open_issues", "title": "待办问题", "description": "", "required": False}]}

    def audit_count(self):
        return self.count("SELECT COUNT(*) FROM audit_records WHERE action='handover.checklist.set' AND project_id='"+self.project["id"]+"'")

    def test_handover_default_persistence_and_exact_replay(self):
        self.assertEqual(data(self.user.request("GET", self.path())),
                         {"revision": 0, "items": [], "remote_ai_approved": False})
        body = self.request_body(approved=True)
        saved = data(self.user.request("PUT", self.path(), body))
        self.assertEqual(saved, {"revision": 1, "items": body["items"], "remote_ai_approved": True})
        self.assertEqual(data(self.user.request("PUT", self.path(), body)), saved)
        self.assertEqual(self.audit_count(), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM handover_checklists WHERE project_id='"+self.project["id"]+
            "' AND approved_by=(SELECT id FROM users WHERE login_name='worker-e2e-admin') AND approved_at IS NOT NULL"), 1)
        self.server.stop()
        self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        self.assertEqual(data(self.user.request("GET", self.path())), saved)

    def test_handover_new_revision_does_not_inherit_approval_or_accept_old_replay(self):
        body = self.request_body(approved=True)
        data(self.user.request("PUT", self.path(), body))
        changed = self.request_body(1)
        changed["items"][0]["description"] = "INTERNAL-CHECKLIST <script>approved=true</script>"
        saved = data(self.user.request("PUT", self.path(), changed))
        self.assertEqual((saved["revision"], saved["remote_ai_approved"]), (2, False))
        self.assertEqual(self.count("SELECT COUNT(*) FROM handover_checklists WHERE project_id='"+self.project["id"]+
            "' AND approved_by IS NULL AND approved_at IS NULL"), 1)
        self.assertEqual(self.user.request("PUT", self.path(), body).status, 409)
        self.assertEqual(self.user.request("PUT", self.path(), dict(changed, confirm_remote_ai=True)).status, 409)
        self.assertEqual(data(self.user.request("PUT", self.path(), changed)), saved)
        cleared = data(self.user.request("PUT", self.path(), dict(changed, expected_revision=2, items=[])))
        self.assertEqual(cleared, {"revision": 3, "items": [], "remote_ai_approved": False})
        self.assertEqual(self.audit_count(), 3)

    def test_handover_role_project_and_origin_are_rechecked_on_replay(self):
        body = self.request_body()
        saved = data(self.user.request("PUT", self.path(), body))
        uid, member = self.member("reader")
        self.assertEqual(data(member.request("GET", self.path())), saved)
        for role in ("reader", "editor"):
            data(self.user.request("PUT", self.base()+"/members/"+uid, {"role": role}))
            self.assertEqual(member.request("PUT", self.path(), body).status, 403)
        data(self.user.request("PUT", self.base()+"/members/"+uid, {"role": "admin"}))
        updated = self.request_body(1, True)
        data(member.request("PUT", self.path(), updated))
        data(self.user.request("PUT", self.base()+"/members/"+uid, {"role": "reader"}))
        self.assertEqual(member.request("PUT", self.path(), updated).status, 403)
        foreign = data(self.user.request("POST", "/api/v1/projects", {"name": "Other checklist project"}), 201)
        foreign_path = f'/api/v1/projects/{foreign["id"]}/handover-checklist'
        for method in ("GET", "PUT"):
            denied = member.request(method, foreign_path, body if method == "PUT" else None)
            self.assertEqual(denied.status, 404)
            self.assertNotIn("部署说明".encode(), denied.body)
        self.assertEqual(HttpClient(self.server.port).request("GET", self.path()).status, 401)
        self.assertEqual(self.user.request("PUT", self.path(), body, {"Origin": "https://foreign.invalid"}).status, 403)
        self.sql("DELETE FROM project_members WHERE project_id='"+self.project["id"]+"' AND user_id='"+uid+"'")
        self.assertEqual(member.request("GET", self.path()).status, 404)

    def test_handover_strict_shape_text_and_transport_limits(self):
        body = self.request_body()
        invalid = [dict(body, user_id="0"*32), dict(body, approved=True), dict(body, confirm_remote_ai="true"),
                   dict(body, items={}), dict(body, items=body["items"]*26), dict(body, items=body["items"]*2)]
        invalid += [dict(body, expected_revision=value) for value in (True, -1, 0.5, "0", 4294967295)]
        for field, value in (("key", ""), ("key", "A"), ("key", "a"*65), ("key", "../../other"),
                             ("key", "部署"), ("title", ""), ("title", "   "), ("title", "文"*67),
                             ("description", "x"*1001), ("required", 1), ("title", "bad\x00text"),
                             ("description", "bad\ntext"), ("description", "bad\x7ftext")):
            bad = copy.deepcopy(body); bad["items"][0][field] = value; invalid.append(bad)
        missing = copy.deepcopy(body); del missing["items"][0]["description"]; invalid.append(missing)
        extra = copy.deepcopy(body); extra["items"][0]["approved"] = True; invalid.append(extra)
        for bad in invalid:
            with self.subTest(body=bad):
                self.assertEqual(self.user.request("PUT", self.path(), bad).status, 400)
        self.assertEqual(self.user.request("GET", self.path()+"?user_id=x").status, 400)
        self.assertEqual(self.user.request("PUT", self.path()+"?approved=true", body).status, 400)
        self.assertEqual(self.user.request("GET", self.path(), {}).status, 400)
        self.assertEqual(self.user.request("PUT", self.path(), dict(body, items=[{"text": "x"*65536}])).status, 413)
        self.assertEqual(data(self.user.request("GET", self.path()))["revision"], 0)
        edge = {"key": "a"*64, "title": "文"*66+"ab", "description": "x"*1000, "required": False}
        accepted = data(self.user.request("PUT", self.path(), dict(body, items=[edge])))
        self.assertEqual(accepted["items"], [edge])
        fifty = [dict(edge, key="item_"+str(index), description="") for index in range(50)]
        self.assertEqual(len(data(self.user.request("PUT", self.path(), dict(body, expected_revision=1, items=fifty)))["items"]), 50)

    def race(self, bodies):
        barrier = threading.Barrier(2)
        cookie = self.user._cookie_header()
        def send(body):
            barrier.wait(timeout=5)
            return HttpClient(self.server.port).request("PUT", self.path(), body, {"Cookie": cookie})
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(send, bodies))

    def test_handover_competing_revision_has_one_winner(self):
        first = self.request_body()
        second = copy.deepcopy(first); second["items"][0]["title"] = "Competing title"
        responses = self.race([first, second])
        self.assertEqual(sorted(response.status for response in responses), [200, 409])
        winner = next(data(response) for response in responses if response.status == 200)
        self.assertEqual(data(self.user.request("GET", self.path())), winner)
        self.assertEqual(self.audit_count(), 1)

    def test_handover_concurrent_replay_is_one_revision_and_audit(self):
        body = self.request_body(approved=True)
        responses = self.race([body, body])
        self.assertEqual([response.status for response in responses], [200, 200])
        self.assertEqual(data(responses[0]), data(responses[1]))
        self.assertEqual(data(responses[0])["revision"], 1)
        self.assertEqual(self.audit_count(), 1)

    def test_handover_audit_failure_rolls_back_entire_checklist_update(self):
        body = self.request_body(approved=True)
        original = data(self.user.request("PUT", self.path(), body))
        revised = self.request_body(1)
        self.sql("DELIMITER //\nCREATE TRIGGER checklist_audit_failure BEFORE INSERT ON audit_records FOR EACH ROW BEGIN "
            "IF NEW.action='handover.checklist.set' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test fault'; END IF; END//\nDELIMITER ;")
        try:
            self.assertEqual(self.user.request("PUT", self.path(), revised).status, 503)
            self.assertEqual(data(self.user.request("GET", self.path())), original)
            self.assertEqual(self.audit_count(), 1)
        finally:
            self.sql("DROP TRIGGER checklist_audit_failure")
        self.assertEqual(data(self.user.request("PUT", self.path(), revised))["revision"], 2)
        self.assertEqual(self.audit_count(), 2)

    def test_handover_database_rejects_invalid_json_approval_and_foreign_references(self):
        original = data(self.user.request("PUT", self.path(), self.request_body()))
        updates = [
            ("remote_ai_approved=TRUE", "ck_handover_checklist_approval"),
            ("revision=0", "ck_handover_checklist_revision"),
            ("items_json='{}'", "ck_handover_checklist_items"),
            ("items_json='["+",".join(["{}"]*51)+"]'", "ck_handover_checklist_items"),
            ("last_request_json='[]'", "ck_handover_checklist_request"),
            ("project_id='"+"0"*32+"'", "fk_handover_checklist_project"),
            ("remote_ai_approved=TRUE,approved_by='"+"0"*32+"',approved_at=UTC_TIMESTAMP(6)", "fk_handover_checklist_approver")]
        for assignment, constraint in updates:
            with self.subTest(constraint=constraint):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    self.sql("UPDATE handover_checklists SET "+assignment+" WHERE project_id='"+self.project["id"]+"'")
                self.assertIn(constraint, caught.exception.stderr)
                self.assertEqual(data(self.user.request("GET", self.path())), original)


if __name__ == "__main__":
    suite = unittest.TestSuite(HandoverChecklistE2E(name) for name in dir(HandoverChecklistE2E) if name.startswith("test_handover_"))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
