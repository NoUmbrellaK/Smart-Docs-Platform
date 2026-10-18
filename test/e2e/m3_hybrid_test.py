#!/usr/bin/env python3
"""Real MCP/C++/MySQL/offline vectors; Agent supplier is local, never a cloud request."""
import asyncio
from datetime import timedelta
import json
import os
import sys
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import m2_vector_worker_test as support
from m1_http_test import data
from smartdocs import agent_cli, worker, vector_worker


class HybridMcpE2E(support.VectorE2E):
    def test_mcp_hybrid_scope_fallback_and_agent_cli(self):
        selected=self.upload("recovery.txt",b"Database disaster recovery: restore the latest backup to recover lost data.","text/plain")
        outside=self.upload("OUTSIDE-TASK.txt",b"OUTSIDE-MARKER database backup recovery guidance.","text/plain")
        self.upload("INTERNAL.txt",b"INTERNAL-MARKER database backup recovery guidance.","text/plain")
        for _ in range(3): self.assertEqual(worker.run_once(self.client),"completed")
        for job in (selected,outside):
            self.policy(job,True); self.jobs.append(self.vector_job(job))
            self.assertEqual(vector_worker.run_once(self.vector,self.encoder),"completed")
        tasks=f'/api/v1/projects/{self.project["id"]}/agent-tasks'
        task=data(self.user.request("POST",tasks,{"question":"Find the public backup recovery instructions.",
            "confirm_remote_ai":True,"versions":[{"file_id":selected["file_id"],"version_id":selected["version_id"]}]}),201)
        context={"backend_port":self.server.port,"origin":f"http://127.0.0.1:{self.server.port}",
            "session_token":next(c.value for c in self.user.cookies if c.name=="smartdocs_session"),
            "project_id":self.project["id"],"task_id":task["id"],"scope":"task_versions"}
        path=self.root/"hybrid-context.json"; path.write_text(json.dumps(context)); path.chmod(0o600)
        socket=os.environ["SMARTDOCS_ENCODER_SOCKET"]

        async def search(socket_path,query):
            parameters=StdioServerParameters(command=sys.executable,
                args=["-B","-m","smartdocs.mcp_read","--context",str(path),"--encoder-socket",socket_path],
                env={"PATH":"/usr/bin:/bin","PYTHONPATH":str(support.support.ROOT/"python")})
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams,read_timeout_seconds=timedelta(seconds=30)) as client:
                    await client.initialize()
                    result=await client.call_tool("search_documents",{"mode":"hybrid","query":query})
                    for forbidden in ("OUTSIDE-MARKER","INTERNAL-MARKER","OUTSIDE-TASK",context["session_token"]):
                        self.assertNotIn(forbidden,result.model_dump_json())
                    return result.structuredContent

        found=asyncio.run(search(socket,"如何恢复备份？"))
        self.assertEqual(found["status"],"success")
        self.assertEqual(found["data"]["retrieval"],"hybrid")
        self.assertEqual(found["data"]["coverage"]["current_files"],1)
        self.assertEqual([item["file_id"] for item in found["data"]["items"]],[selected["file_id"]])
        self.assertEqual(found["data"]["items"][0]["match_sources"],["vector"])
        degraded=asyncio.run(search(str(self.root/"missing.sock"),"backup"))
        self.assertEqual(degraded["data"]["retrieval"],"keyword_only")
        self.assertIn("query_embedding_unavailable",degraded["data"]["warnings"])
        self.assertEqual([item["file_id"] for item in degraded["data"]["items"]],[selected["file_id"]])

        # Debug tool reads claim their task; the Agent must claim its own fresh pending task.
        data(self.user.request("POST",tasks+"/"+task["id"]+"/cancel"))
        task=data(self.user.request("POST",tasks,{"question":"Find the public backup recovery instructions.",
            "confirm_remote_ai":True,"versions":[{"file_id":selected["file_id"],"version_id":selected["version_id"]}]}),201)
        context["task_id"]=task["id"]; path.write_text(json.dumps(context))

        seen=[]
        async def local_model(messages,**kwargs):
            seen.append(messages[-1])
            if len(seen)==1:
                return {"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":None,
                    "tool_calls":[{"id":"query_1","type":"function","function":{"name":"search_documents",
                    "arguments":json.dumps({"mode":"hybrid","query":"如何恢复备份？"})}}]}}]}
            result=json.loads(messages[-1]["content"])["data"]
            self.assertEqual(result["retrieval"],"hybrid")
            self.assertEqual([item["file_id"] for item in result["items"]],[selected["file_id"]])
            chunk=result["items"][0]["chunk"]
            return {"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":json.dumps({
                "answer":"Restore the latest backup to recover lost data.",
                "citations":[{k:chunk[k] for k in ("file_id","version_id","chunk_id")}]})}}]}

        audit=self.root/"hybrid-audit.jsonl"
        # Only the paid model boundary is replaced; CLI, child, query encoder, C++ and storage stay real.
        with patch.object(agent_cli,"DeepSeekModel",return_value=local_model):
            outcome=asyncio.run(agent_cli.run_context(path,audit,"local-test-not-a-provider-key",encoder_socket=socket))
        self.assertEqual(outcome,{"state":"completed"}); self.assertEqual(len(seen),2)
        saved=data(self.user.request("GET",tasks+"/"+task["id"]+"/run/result"))
        self.assertEqual(saved["result"]["citations"][0]["version_id"],selected["version_id"])
        self.assertEqual(saved["model_calls"],2)
        trace=audit.read_text()
        self.assertIn('"tool_result"',trace)
        self.assertNotIn(context["session_token"],trace)
        self.assertNotIn("Restore the latest",trace)
        completed_path=tasks+"/"+task["id"]+"/run/result"
        probe=data(self.user.request("POST",tasks,{"question":"Verify revocation blocks search.",
            "confirm_remote_ai":True,"versions":[{"file_id":selected["file_id"],"version_id":selected["version_id"]}]}),201)
        context["task_id"]=probe["id"]; path.write_text(json.dumps(context))
        self.policy(selected,False)
        denied=asyncio.run(search(socket,"backup"))
        self.assertEqual(denied["status"],"conflict"); self.assertNotIn("data",denied)
        self.assertEqual(self.user.request("GET",completed_path).status,409)
        print("MCP hybrid: real vector-only Chinese query; task scope; missing-socket fallback; CLI cited result; revocation PASS",flush=True)


if __name__=="__main__":
    suite=unittest.TestSuite([HybridMcpE2E("test_mcp_hybrid_scope_fallback_and_agent_cli")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
