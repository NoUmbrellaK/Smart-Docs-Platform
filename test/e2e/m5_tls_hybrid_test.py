"""TLS with real offline vectors and scoped MCP; only paid provider transport is replaced."""
import argparse
from functools import partial
import json
import os
from pathlib import Path
import unittest

from m1_http_test import complete, create_upload, data, upload_parts
from m3_agent_http_test import tool
from m5_tls_gateways_test import TLSGatewaysE2E, tls_gateways
from smartdocs import vector_worker, worker


class TLSHybridE2E(TLSGatewaysE2E):
    def test_tls_real_hybrid_scope_and_new_version(self):
        # Broken vector routing, omitted task scope or stale version admission must fail this case.
        async def respond(body):
            if len(self.calls) == 1:
                return tool("search_documents", {"mode": "hybrid", "query": "如何恢复备份？"})
            result = json.loads(body["messages"][-1]["content"])["data"]
            self.assertEqual(result["retrieval"], "hybrid")
            self.assertEqual([hit["file_id"] for hit in result["items"]], [selected["file_id"]])
            self.assertEqual(result["items"][0]["match_sources"], ["vector"])
            for marker in ("OUTSIDE-MARKER", "INTERNAL-MARKER"):
                self.assertNotIn(marker, json.dumps(body))
            chunk = result["items"][0]["chunk"]
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({
                    "answer": "Restore the latest backup to recover lost data.",
                    "citations": [{k: chunk[k] for k in ("file_id", "version_id", "chunk_id")}]})}}]}

        with tls_gateways(self, respond, encoder_socket=self.encoder_socket):
            selected = self.upload("recovery.txt",
                b"Database disaster recovery: restore the latest backup to recover lost data.", "text/plain")
            outside = self.upload("outside.txt", b"OUTSIDE-MARKER database backup recovery guidance.", "text/plain")
            internal = self.upload("internal.txt", b"INTERNAL-MARKER database backup recovery guidance.", "text/plain")
            for _ in range(3):
                self.assertEqual(worker.run_once(self.client), "completed")
            vector = vector_worker.VectorClient(self.server.port, self.token)
            encoder = partial(vector_worker.request_encoding, self.encoder_socket)
            self.assertEqual(vector_worker.run_once(vector, encoder), "idle")
            for job in (selected, outside):
                self.policy(job, True)
                self.jobs.append({**job, "processing_job_id": self.document(job)["vector_job_id"]})
                self.assertEqual(vector_worker.run_once(vector, encoder), "completed")
                self.assertEqual(self.document(job)["vector_state"], "completed")
            search_path = self.base() + "/hybrid-search"
            found = data(self.user.request("POST", search_path, {"query": "如何恢复备份？"}))
            self.assertEqual(found["retrieval"], "hybrid")
            self.assertEqual({hit["file_id"] for hit in found["items"]},
                             {selected["file_id"], outside["file_id"]})
            self.assertTrue(all(hit["match_sources"] == ["vector"] for hit in found["items"]))
            # Same lexical match: ordinary local reading permits internal content; AI scope does not.
            local_path = self.base() + "/search"
            local = data(self.user.request("POST", local_path, {"mode": "keyword", "query": "backup"}))
            self.assertEqual({hit["file_id"] for hit in local["items"]},
                             {selected["file_id"], outside["file_id"], internal["file_id"]})
            allowed = data(self.user.request("POST", local_path, {"mode": "hybrid", "query": "backup"}))
            self.assertEqual({hit["file_id"] for hit in allowed["items"]},
                             {selected["file_id"], outside["file_id"]})
            print("PASS TLS real vector publication and Chinese vector-only retrieval; internal excluded", flush=True)
            task = self.create_task(selected)
            path = self.tasks_path() + "/" + task["id"]
            outcome = self.user.request("POST", path + "/execute", {})
            self.assertEqual((outcome.status, outcome.json()), (200, {"data": {"state": "completed"}}),
                             {"provider_calls": len(self.calls), "fixture_errors": self.model_errors})
            self.assertEqual(len(self.calls), 2)
            saved = data(self.user.request("GET", path + "/run/result"))
            self.assertEqual(saved["model_calls"], 2)
            citation = saved["result"]["citations"][0]
            self.assertEqual(citation["version_id"], selected["version_id"])
            source_path = self.version_path(selected) + "/chunks/" + citation["chunk_id"]
            source = data(self.user.request("GET", source_path))
            self.assertIn("restore the latest backup", source["chunk"]["text"])
            trace = data(self.user.request("GET", path + "/tool-calls"))
            # One MCP hybrid call does a preflight and a fresh post-encoding backend read.
            self.assertEqual([item["tool"] for item in trace["items"]],
                             ["search_documents", "search_documents"])
            self.assertEqual([item["sequence"] for item in trace["items"]], [1, 2])
            self.assertTrue(all(item["status"] == "success" for item in trace["items"]))
            print("PASS TLS Agent uses real hybrid MCP restricted to task version with saved fixed source", flush=True)
            body = b"New recovery instructions awaiting explicit approval."
            upload = create_upload(self.user, self.project["id"], self.project["root_directory_id"],
                                   "recovery.txt", body, "text/plain", selected["file_id"], selected["version_id"])
            upload_parts(self.user, self.project["id"], upload, body)
            latest = data(complete(self.user, self.project["id"], upload["task_id"]))
            self.jobs.append(latest)
            self.assertEqual(worker.run_once(self.client), "completed")
            self.assertEqual(self.document(latest)["vector_state"], "not_applicable")
            found = data(self.user.request("POST", search_path, {"query": "如何恢复备份？"}))
            self.assertEqual([hit["file_id"] for hit in found["items"]], [outside["file_id"]])
            historical = data(self.user.request("GET", source_path))
            self.assertFalse(historical["is_current"])
            self.assertEqual(historical["chunk"], source["chunk"])
            self.assertEqual(self.user.request("GET", path + "/run/result").status, 409)
            self.policy(outside, False)
            found = data(self.user.request("POST", search_path, {"query": "如何恢复备份？"}))
            self.assertEqual(found["items"], [])
            self.assertEqual(vector_worker.run_once(vector, encoder), "idle")
            self.assertEqual(len(self.calls), 2)
            self.assertEqual(self.model_errors, [])
            print("PASS TLS superseded source excluded; no inherited approval; fixed history and revocation", flush=True)
        print(json.dumps({"case": "tls-real-hybrid", "result": "pass", "checks": 3,
                          "document_encoding_batches": 2, "mock_provider_responses": 2,
                          "remote_model_calls": 0, "production_deployment": "not_run"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", required=True, type=Path)
    parser.add_argument("--encoder-socket", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("run as an unprivileged isolated test account")
    TLSHybridE2E.nginx = args.nginx.resolve()
    TLSHybridE2E.encoder_socket = str(args.encoder_socket.resolve())
    # Exercise both opt-in and unchanged default; each method creates its own project.
    suite = unittest.TestSuite([TLSHybridE2E("test_tls_real_hybrid_scope_and_new_version"),
                               TLSHybridE2E("test_tls_real_gateways_and_revocation")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
