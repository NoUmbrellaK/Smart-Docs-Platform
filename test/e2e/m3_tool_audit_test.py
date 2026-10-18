"""Server-recorded backend tool calls over real HTTP/isolated MySQL; no model."""
from concurrent.futures import ThreadPoolExecutor
import secrets
import unittest

from m1_http_test import HttpClient, create_user, data, login
from m2_worker_test import ROOT
from m3_task_list_test import TaskListE2E
from smartdocs import worker


class ToolAuditE2E(TaskListE2E):
    def prepare(self):
        job = self.approved_file()
        task = self.create_task(job)
        path = self.tasks_path()+"/"+task["id"]
        self.assertEqual(data(self.user.request("GET", path+"/tool-calls")),
                         {"items": [], "capacity": 64, "kind": "backend_requests"})
        return job, task, path

    def test_audit_real_calls_errors_and_restart_are_persistent_and_redacted(self):
        job, task, path = self.prepare()
        self.assertEqual(worker.run_once(self.client), "completed")
        result = self.user.request("POST", path+"/tools/search", {"mode": "keyword", "query": "SOURCE"})
        chunk = data(result)["items"][0]["chunk"]["chunk_id"]
        read = self.user.request("POST", path+"/tools/read", {
            "file_id": job["file_id"], "version_id": job["version_id"], "chunk_id": chunk})
        self.assertEqual(data(read)["chunk"]["text"], "SOURCE-MARKER")
        rejected = self.user.request("POST", path+"/tools/read", {"file_id": "bad", "secret": "DO-NOT-LOG"})
        self.assertEqual(rejected.status, 400)
        trace = data(self.user.request("GET", path+"/tool-calls"))
        rows = trace["items"]
        self.assertEqual([x["sequence"] for x in rows], [1, 2, 3])
        self.assertEqual([x["tool"] for x in rows], ["search_documents", "read_document_version", "read_document_version"])
        self.assertEqual([x["status"] for x in rows], ["success", "success", "invalid_request"])
        self.assertEqual(rows[0]["request_id"], result.json()["request_id"])
        self.assertEqual(rows[1]["request_id"], read.json()["request_id"])
        self.assertEqual(rows[2]["request_id"], rejected.json()["request_id"])
        self.assertEqual(rows[0]["parameters"], {"mode": "keyword", "query_bytes": 6})
        self.assertEqual(rows[1]["parameters"], {"selector": "chunk"})
        for row in rows:
            self.assertEqual(set(row), {"task_id", "sequence", "request_id", "tool", "parameters", "status", "started_at", "duration_ms"})
            self.assertEqual(row["task_id"], task["id"])
            self.assertIsInstance(row["duration_ms"], int)
            self.assertGreaterEqual(row["duration_ms"], 0)
            self.assertRegex(row["request_id"], r"^[0-9a-f]{32}$")
        raw = self.user.request("GET", path+"/tool-calls").body
        for marker in (b"SOURCE", b"PRIVATE-FILENAME", b"QUESTION-MARKER", b"DO-NOT-LOG", self.token.encode()):
            self.assertNotIn(marker, raw)
        self.server.stop()
        self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        self.assertEqual(data(self.user.request("GET", path+"/tool-calls")), trace)
        self.assertEqual(data(self.user.request("GET", path))["state"], "interrupted")
        self.assertEqual(self.user.request("POST", path+"/tools/search", {"mode":"metadata", "query":""}).status, 409)
        self.assertEqual(data(self.user.request("GET", path+"/tool-calls")), trace)

    def test_audit_owner_scope_membership_and_read_only_route(self):
        job, task, path = self.prepare()
        data(self.user.request("POST", path+"/tools/read", {"file_id": job["file_id"], "version_id": job["version_id"]}))
        pending = data(self.user.request("GET", path+"/tool-calls"))["items"][0]
        self.assertEqual((pending["status"],pending["parameters"]), ("processing",{"selector":"status"}))
        data(self.user.request("POST", path+"/tools/search", {"mode": "metadata", "query": ""}))
        name = "audit-reader-"+secrets.token_hex(4)
        uid = create_user(str(ROOT/"bin/smartdocs-admin"), name, self.password)
        member = f'/api/v1/projects/{self.project["id"]}/members/{uid}'
        data(self.user.request("PUT", member, {"role": "reader"}))
        reader = HttpClient(self.server.port)
        login(reader, name, self.password)
        own = self.create_task(job, reader)
        own_path = self.tasks_path()+"/"+own["id"]
        for client, target in ((reader,path), (self.user,own_path)):
            self.assertEqual(client.request("GET", target+"/tool-calls").status, 404)
        self.assertEqual(HttpClient(self.server.port).request("GET", path+"/tool-calls").status, 401)
        self.assertEqual(self.user.request("POST", path+"/tool-calls", {"status":"success"}).status, 405)
        self.assertEqual(self.user.request("GET", path+"/tool-calls?after=1").status, 400)
        self.assertEqual(self.user.request("GET", path+"/tool-calls", {}).status, 400)
        self.policy(job, False)
        self.assertEqual(self.user.request("GET", path+"/tool-calls").status, 409)
        self.policy(job, True)
        self.assertEqual(len(data(self.user.request("GET", path+"/tool-calls"))["items"]), 2)
        self.sql("DELETE FROM project_members WHERE project_id='"+self.project["id"]+"' AND user_id='"+uid+"'")
        self.assertEqual(reader.request("GET", own_path+"/tool-calls").status, 404)

    def test_audit_persistence_failure_never_admits_or_returns_source(self):
        job, _, path = self.prepare()
        self.assertEqual(worker.run_once(self.client), "completed")
        hits = data(self.user.request("POST", f'/api/v1/projects/{self.project["id"]}/search',
                                      {"mode":"keyword", "query":"SOURCE"}))
        chunk = hits["items"][0]["chunk"]["chunk_id"]
        self.sql("CREATE TRIGGER fail_agent_audit_insert BEFORE INSERT ON agent_tool_calls FOR EACH ROW "
                 "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test only'")
        try:
            response = self.user.request("POST", path+"/tools/search", {"mode":"keyword", "query":"SOURCE"})
            self.assertGreaterEqual(response.status, 500)
            self.assertNotIn(b"SOURCE-MARKER", response.body)
            self.assertEqual(data(self.user.request("GET", path))["state"], "pending")
            self.assertEqual(data(self.user.request("GET", path+"/tool-calls"))["items"], [])
        finally:
            self.sql("DROP TRIGGER fail_agent_audit_insert")
        self.sql("CREATE TRIGGER fail_agent_audit_update BEFORE UPDATE ON agent_tool_calls FOR EACH ROW "
                 "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test only'")
        try:
            response = self.user.request("POST", path+"/tools/read", {
                "file_id":job["file_id"], "version_id":job["version_id"], "chunk_id":chunk})
            self.assertGreaterEqual(response.status, 500)
            self.assertNotIn(b"SOURCE-MARKER", response.body)
            self.assertNotIn("data", response.json())
            rows = data(self.user.request("GET", path+"/tool-calls"))["items"]
            self.assertEqual([(x["status"],x["duration_ms"]) for x in rows], [("unknown",None)])
            self.assertEqual(response.json()["request_id"], rows[0]["request_id"])
        finally:
            self.sql("DROP TRIGGER fail_agent_audit_update")
        data(self.user.request("POST", path+"/cancel"))

    def test_audit_capacity_concurrent_sequence_and_unknown_is_not_success(self):
        _, task, path = self.prepare()
        def call(_):
            client = HttpClient(self.server.port)
            client.cookies = self.user.cookies
            return client.request("POST", path+"/tools/search", {"mode":"metadata", "query":""}).status
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(call, range(64))), [200]*64)
        self.assertEqual(call(None), 429)
        rows = data(self.user.request("GET", path+"/tool-calls"))["items"]
        self.assertEqual([x["sequence"] for x in rows], list(range(1,65)))
        self.assertEqual(len({x["request_id"] for x in rows}), 64)
        # Persisted attempt-only fixture models missing completion, not an actual crash test.
        self.sql("UPDATE agent_tool_calls SET status='unknown',duration_ms=NULL WHERE task_id='"+task["id"]+"' AND sequence=64")
        self.server.stop()
        self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN": self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        last = data(self.user.request("GET", path+"/tool-calls"))["items"][-1]
        self.assertEqual((last["status"],last["duration_ms"]), ("unknown",None))


if __name__ == "__main__":
    suite = unittest.TestSuite(ToolAuditE2E(name) for name in dir(ToolAuditE2E) if name.startswith("test_audit_"))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
