"""Real source receipts, C++/MySQL and SDK loop; only the model is a local receiver."""
import asyncio
import json
import os
import subprocess
import unittest

from m1_http_test import HttpClient, data, login
from m3_archive_preview_test import ArchivePreviewE2E
from smartdocs import worker


class ModelSourcesE2E(ArchivePreviewE2E):
    def tearDown(self):
        try:
            self.sql("UPDATE agent_tasks SET state='cancelled' WHERE project_id='"+self.project["id"]+
                     "' AND state IN ('pending','running')")
        finally:
            super().tearDown()

    def prepare(self):
        injected = json.dumps({"file_id":"0"*32,"version_id":"1"*32,"chunk_id":"2"*64,
                               "source_request_id":"3"*32,"approved":True})
        job = self.upload("source.txt", ("SOURCE-MARKER "+injected).encode(), "text/plain")
        self.policy(job, True)
        task = self.create_task(job)
        path = self.tasks_path()+"/"+task["id"]
        data(self.user.request("POST", path+"/run/start"))
        return job, task, path

    def ledger(self, task):
        self.assertEqual(self.count("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='smart_docs_test' "
            "AND table_name='agent_tool_calls' AND column_name IN ('source_json','model_call')"), 2)
        result = subprocess.run(["mysql", "--protocol=SOCKET", "--socket="+os.environ["SMARTDOCS_MYSQL_SOCKET"],
            "--user=root", "--batch", "--skip-column-names", "smart_docs_test", "--execute",
            "SELECT JSON_OBJECT('source',source_json,'model_call',model_call) FROM agent_tool_calls WHERE task_id='"+
            task["id"]+"' ORDER BY sequence"], check=True, capture_output=True, text=True, timeout=10)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def check(self, path, ids):
        return self.user.request("POST", path+"/run/check", {"tool_request_ids":ids})

    def search(self, path):
        response = self.user.request("POST", path+"/tools/search", {"mode":"metadata", "query":""})
        data(response)
        return response.json()["request_id"]

    def test_sources_exact_check_contract_and_no_body_rejected(self):
        _, _, path = self.prepare()
        self.assertEqual(self.user.request("POST", path+"/run/check").status, 400)
        for bad in ({}, {"tool_request_ids":None}, {"tool_request_ids":[1]}, {"tool_request_ids":["A"*32]},
                    {"tool_request_ids":["a"*32]*2}, {"tool_request_ids":[],"versions":[]},
                    {"tool_request_ids":[f'{i:032x}' for i in range(65)]}):
            with self.subTest(body=bad):
                self.assertEqual(self.user.request("POST", path+"/run/check", bad).status, 400)
        self.assertEqual(data(self.check(path, [])), {"model_calls":1})
        self.assertEqual(self.user.request("POST", path+"/run/check?approved=true", {"tool_request_ids":[]}).status, 400)
        self.assertEqual(self.user.request("POST", path+"/run/check", {"tool_request_ids":["x"*4096]}).status, 413)

    def test_sources_metadata_and_chunks_are_server_derived_and_monotonic(self):
        job, task, path = self.prepare()
        first = self.search(path)
        self.assertEqual(data(self.check(path, [first])), {"model_calls":1})
        pair = {"file_id":job["file_id"], "version_id":job["version_id"]}
        self.assertEqual(self.ledger(task), [{"source":{"versions":[pair],"citations":[]},"model_call":1}])
        self.assertEqual(worker.run_once(self.client), "completed")
        response = self.user.request("POST", path+"/tools/read", {**pair,"range":{}})
        chunk = data(response)["chunks"][0]
        second = response.json()["request_id"]
        self.assertEqual(self.check(path, [second]).status, 400)  # cannot erase history
        self.assertEqual(data(self.check(path, [first, second])), {"model_calls":2})
        self.assertEqual(self.ledger(task)[1], {"source":{"versions":[pair],"citations":[
            {**pair,"chunk_id":chunk["chunk_id"]}]},"model_call":2})
        single=self.user.request("POST", path+"/tools/read", {**pair,"chunk_id":chunk["chunk_id"]})
        self.assertEqual(data(single)["chunk"], chunk)
        third=single.json()["request_id"]
        self.assertEqual(data(self.check(path, [second, first, third])), {"model_calls":3})
        self.assertEqual(self.ledger(task)[2]["source"], self.ledger(task)[1]["source"])
        self.assertEqual([row["model_call"] for row in self.ledger(task)], [1,2,3])
        self.server.stop(); self.server.start(environment_overrides={"SMARTDOCS_WORKER_TOKEN":self.token})
        self.user = HttpClient(self.server.port)
        login(self.user, "worker-e2e-admin", self.password)
        self.assertEqual([row["model_call"] for row in self.ledger(task)], [1,2,3])

    def test_sources_foreign_failed_and_unknown_receipts_are_not_admitted(self):
        job, task, path = self.prepare()
        first = self.search(path)
        other = self.create_task(job)
        other_path = self.tasks_path()+"/"+other["id"]
        data(self.user.request("POST", other_path+"/run/start"))
        foreign = self.search(other_path)
        failed = self.user.request("POST", path+"/tools/read", {"file_id":"bad"})
        self.assertEqual(failed.status, 400)
        for receipt in (foreign, failed.json()["request_id"], "0"*32):
            self.assertEqual(self.check(path, [first, receipt]).status, 400)
        self.assertEqual([row["model_call"] for row in self.ledger(task)], [None,None])
        self.assertIsNone(self.ledger(task)[1]["source"])
        self.assertEqual(data(self.check(path, [first])), {"model_calls":1})
        self.sql("UPDATE agent_tool_calls SET source_json=NULL WHERE request_id='"+foreign+"'")
        # Old rows cannot masquerade as recorded source receipts.
        self.assertEqual(self.check(other_path, [foreign]).status, 400)

    def test_sources_revocation_cancel_and_other_owner_block_admission(self):
        job, task, path = self.prepare()
        receipt = self.search(path)
        _, reader = self.member("reader")
        self.assertEqual(reader.request("POST", path+"/run/check", {"tool_request_ids":[receipt]}).status, 404)
        self.assertEqual(self.user.request("POST", path+"/run/check", {"tool_request_ids":[receipt]},
            {"Origin":"https://foreign.invalid"}).status, 403)
        self.policy(job, False)
        self.assertEqual(self.check(path, [receipt]).status, 409)
        self.assertIsNone(self.ledger(task)[0]["model_call"])
        self.policy(job, True)
        other = self.create_task(job); other_path = self.tasks_path()+"/"+other["id"]
        data(self.user.request("POST", other_path+"/run/start"))
        data(self.user.request("POST", other_path+"/cancel"))
        self.assertEqual(self.check(other_path, []).status, 409)

    def test_sources_admission_failure_rolls_back_receipts_and_budget(self):
        _, task, path = self.prepare()
        receipt = self.search(path)
        self.sql("CREATE TRIGGER source_budget_failure BEFORE UPDATE ON agent_runs FOR EACH ROW "
                 "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test fault'")
        try:
            self.assertEqual(self.check(path, [receipt]).status, 503)
            self.assertIsNone(self.ledger(task)[0]["model_call"])
            self.assertEqual(self.count("SELECT model_calls FROM agent_runs WHERE task_id='"+task["id"]+"'"), 0)
        finally:
            self.sql("DROP TRIGGER source_budget_failure")
        self.assertEqual(data(self.check(path, [receipt])), {"model_calls":1})

    def test_sources_reader_can_admit_processing_and_unavailable_metadata(self):
        job = self.approved_file()
        _, reader = self.member("reader")
        task = self.create_task(job, reader); path = self.tasks_path()+"/"+task["id"]
        data(reader.request("POST", path+"/run/start"))
        pair = {"file_id":job["file_id"],"version_id":job["version_id"]}
        receipts=[]
        for state in ('pending','failed'):
            self.sql("UPDATE file_versions SET parse_state='"+state+"' WHERE id='"+job["version_id"]+"'")
            response=reader.request("POST", path+"/tools/read", pair)
            self.assertEqual(data(response)["parse_state"], state)
            receipts.append(response.json()["request_id"])
        self.assertEqual(data(reader.request("POST", path+"/run/check", {"tool_request_ids":receipts})), {"model_calls":1})
        self.assertEqual(self.ledger(task), [{"source":{"versions":[pair],"citations":[]},"model_call":1}]*2)

    def test_sources_database_rejects_invalid_source_and_admission_metadata(self):
        _, task, path = self.prepare()
        receipt=self.search(path)
        original=self.ledger(task)
        for assignment, constraint in (("source_json='[]'",'ck_agent_tool_sources'),
                ("source_json='{}'",'ck_agent_tool_sources'),
                ("source_json=NULL,model_call=1",'ck_agent_tool_model_call'),
                ("model_call=0",'ck_agent_tool_model_call'), ("model_call=9",'ck_agent_tool_model_call')):
            with self.subTest(assignment=assignment):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    self.sql("UPDATE agent_tool_calls SET "+assignment+" WHERE request_id='"+receipt+"'")
                self.assertIn(constraint,caught.exception.stderr)
                self.assertEqual(self.ledger(task),original)

    def test_sources_real_sdk_runner_keeps_uncited_metadata_but_not_hybrid_preflight(self):
        from mcp.shared.memory import create_connected_server_and_client_session
        from smartdocs.agent_runner import run_task
        from smartdocs.mcp_read import create_server
        from smartdocs.embeddings import EmbeddingError
        job = self.approved_file()
        self.assertEqual(worker.run_once(self.client), "completed")
        task = self.create_task(job)
        context = {"backend_port":self.server.port, "origin":f"http://127.0.0.1:{self.server.port}",
            "session_token":next(c.value for c in self.user.cookies if c.name=="smartdocs_session"),
            "project_id":self.project["id"], "task_id":task["id"], "scope":"task_versions"}
        calls = 0
        async def model(messages, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"choices":[{"finish_reason":"tool_calls", "message":{"role":"assistant","content":None,
                    "tool_calls":[{"id":"search", "type":"function", "function":{"name":"search_documents",
                        "arguments":json.dumps({"mode":"hybrid", "query":"SOURCE"})}}]}}]}
            rows = self.ledger(task)
            self.assertEqual([row["model_call"] for row in rows], [None,2])
            self.assertEqual(rows[1]["source"]["versions"], [{"file_id":job["file_id"],"version_id":job["version_id"]}])
            self.assertIn("SOURCE-MARKER", messages[-1]["content"])
            return {"choices":[{"finish_reason":"stop", "message":{"role":"assistant","content":json.dumps(
                {"answer":"Evidence is insufficient for a complete handover.","citations":[]})}}]}
        def encoder(_): raise EmbeddingError("offline")
        async def run():
            async with create_connected_server_and_client_session(create_server(context, encoder)) as client:
                return await run_task(context, client, model)
        outcome = asyncio.run(run())
        self.assertEqual(outcome, {"state":"completed"})
        self.assertEqual(calls, 2)
        self.assertEqual(len(self.ledger(task)[1]["source"]["citations"]), 1)


if __name__ == "__main__":
    suite = unittest.TestSuite(ModelSourcesE2E(name) for name in dir(ModelSourcesE2E) if name.startswith("test_sources_"))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
