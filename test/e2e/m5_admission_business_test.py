"""Measure the three frozen admission-denial cases; no Agent, provider or confirmation."""
import hashlib
import json
import os
import unittest

from m1_http_test import data
from m3_fixed_inputs_test import FixedInputsE2E, TASKS


class AdmissionBusinessE2E(FixedInputsE2E):
    def rejected_case(self, case_id, status):
        # Incorrect role/approval checks, denial with a hidden move, or denial of
        # ordinary authorized download must fail against these frozen inputs.
        case = next(case for case in TASKS if case["id"] == case_id)
        prepared = self.prepare_case(case)
        actor = prepared["actor"]
        before = data(actor.request("GET", self.base() + "/files"))
        self.assertEqual(len(before["items"]), 1)
        self.assertEqual(
            {(item["id"], item["directory_id"], item["current_version_id"]) for item in before["items"]},
            {(prepared["mapping"][ref]["file_id"], prepared["mapping"][ref]["directory_id"],
              prepared["mapping"][ref]["version_id"]) for ref in case["scope"]})
        user = data(actor.request("GET", "/api/v1/me"))
        self.assertEqual([(p["id"], p["role"]) for p in user["projects"]],
                         [(self.project["id"], case["role"])])

        downloads = []
        for ref in case["scope"]:
            source = prepared["mapping"][ref]
            path = self.base() + "/files/" + source["file_id"] + "/versions/" + source["version_id"]
            response = actor.request("GET", path + "/content")
            self.assertEqual(response.status, 200)
            self.assertEqual(hashlib.sha256(response.body).hexdigest(), source["sha256"])
            downloads.append({"fixture": ref, "http": response.status,
                              "sha256": hashlib.sha256(response.body).hexdigest()})
            if case_id in ("Q08", "Q09"):
                self.assertEqual(actor.request("POST", path + "/remote-ai/read", {}).status, 404)

        # prepare_case already checks admission once. This second, explicitly
        # measured attempt is bracketed by actual reads; it is not a new task.
        denied = actor.request("POST", self.tasks_path(), prepared["body"])
        self.assertEqual(denied.status, status)
        after = data(actor.request("GET", self.base() + "/files"))
        self.assertEqual(after, before)
        self.assertEqual(data(actor.request("GET", self.tasks_path()))["items"], [])
        for ref in case["scope"]:
            source = prepared["mapping"][ref]
            path = self.base() + "/files/" + source["file_id"] + "/versions/" + source["version_id"]
            response = actor.request("GET", path + "/content")
            self.assertEqual(response.status, 200)
            self.assertEqual(hashlib.sha256(response.body).hexdigest(), source["sha256"])
        counts = {table: self.count("SELECT COUNT(*) FROM " + table) for table in (
            "agent_tasks", "agent_runs", "agent_tool_calls", "archive_plans",
            "archive_decisions", "handover_reports")}
        self.assertTrue(all(count == 0 for count in counts.values()), counts)
        state = lambda files: [{key: item[key] for key in ("id", "directory_id", "current_version_id")}
                               for item in files["items"]]
        print(json.dumps({
            "case_id": case_id, "role": case["role"], "admission_http": denied.status,
            "setup_admission_http": prepared["admission_http"], "creation_attempts": 2,
            "ordinary_downloads": downloads, "files_before": state(before), "files_after": state(after),
            "content_hashes_unchanged": True, "business_rows": counts, "remote_model_calls": 0,
            "actual_citations": 0, "contract_checks": "pass", "human_review": "not_performed",
        }, sort_keys=True), flush=True)

    def test_q08_unapproved_new_version_is_denied_without_inheriting_approval(self):
        self.rejected_case("Q08", 404)

    def test_q09_editor_can_download_internal_source_but_cannot_send_it_to_ai(self):
        self.rejected_case("Q09", 404)

    def test_a09_reader_archive_rejection_does_not_move_or_overwrite(self):
        self.rejected_case("A09", 403)


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("run as an unprivileged isolated test account")
    # Do not inherit/run the unrelated preflight and earlier-stage test methods.
    suite = unittest.TestSuite(AdmissionBusinessE2E(name) for name in AdmissionBusinessE2E.__dict__
                               if name.startswith("test_"))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2, failfast=True).run(suite).wasSuccessful())
