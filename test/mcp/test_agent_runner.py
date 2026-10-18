"""Real loop and MCP SDK; only the external model and authority are local receivers."""
import asyncio
import copy
import importlib
import importlib.util
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp.shared.memory import create_connected_server_and_client_session
from smartdocs.mcp_read import create_server

TASK, PROJECT, FILE, VERSION, CHUNK = "a"*32, "b"*32, "c"*32, "d"*32, "e"*64
CITATION = {"file_id": FILE, "version_id": VERSION, "chunk_id": CHUNK}


def reply(content=None, calls=None, finish=None):
    message={"role":"assistant","content":content}
    if calls is not None: message["tool_calls"]=calls
    return {"choices":[{"message":message,"finish_reason":finish or ("tool_calls" if calls else "stop")}],
            "usage":{"prompt_tokens":20,"completion_tokens":10,"total_tokens":30}}


def call(name="search_documents", arguments=None, ident="call_1"):
    return {"id":ident,"type":"function","function":{"name":name,
        "arguments":json.dumps(arguments or {"mode":"metadata","query":""})}}


class AgentRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.agent_runner"),"bounded Agent is not implemented")
        self.agent=importlib.import_module("smartdocs.agent_runner")
        self.state="pending"; self.calls=0; self.requests=[]; self.result=None; self.denied=False; self.drop_completion=False
        self.delay_completion=False; self.release_completion=threading.Event()
        self.reject_source=False
        owner=self
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self): self.respond()
            def do_POST(self): self.respond()
            def respond(self):
                raw=self.rfile.read(int(self.headers.get("Content-Length","0")))
                body=json.loads(raw) if raw else None
                owner.requests.append((self.path,body))
                status=200; payload={}
                if self.path.endswith("/run/start"):
                    owner.state="running"; payload={"question":"find public deployment instructions","state":"running"}
                elif self.path.endswith("/run/check"):
                    if owner.denied:
                        status=409; payload={"code":"task_scope_changed"}; owner.state="failed"
                    else: owner.calls+=1; payload={"model_calls":owner.calls}
                elif self.path.endswith("/run/finish"):
                    if owner.delay_completion and body["status"]=="completed":
                        owner.delay_completion=False
                        self.connection.shutdown(socket.SHUT_RDWR); self.connection.close()
                        owner.release_completion.wait(10)
                        owner.state=body["status"]; owner.result=body["result"]
                        return
                    if owner.reject_source and body["status"]=="completed":
                        status=400; payload={"code":"invalid_source"}
                    elif owner.state=="completed" and body["status"]!="completed":
                        status=409; payload={"code":"task_inactive"}
                    else:
                        owner.state=body["status"]; owner.result=body["result"]
                        payload={"state":owner.state}
                    if owner.drop_completion and body["status"]=="completed":
                        self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
                elif self.path.endswith("/tools/search"):
                    payload={"items":[] if body["query"]=="missing" else [{"file_id":FILE,"version_id":VERSION,
                        "chunk":{**CITATION,"text":"Public deployment: run documented checks."}}],"coverage":{"current_files":1}}
                elif self.path.endswith("/tools/read"):
                    payload={"parse_state":"completed","chunks":[{**CITATION,"text":"Public deployment: run documented checks."}]}
                else: payload={"state":owner.state}
                data=json.dumps({"data" if status==200 else "error":payload}).encode()
                self.send_response(status); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
        self.http=ThreadingHTTPServer(("127.0.0.1",0),Receiver)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True); self.thread.start()
        self.context={"backend_port":self.http.server_port,"origin":"http://127.0.0.1:"+str(self.http.server_port),
                      "session_token":"f"*64,"project_id":PROJECT,"task_id":TASK,"scope":"task_versions"}

    def tearDown(self):
        if hasattr(self,"release_completion"): self.release_completion.set()
        if hasattr(self,"http"): self.http.shutdown(); self.http.server_close(); self.thread.join()

    async def run_model(self,model):
        async with create_connected_server_and_client_session(create_server(self.context)) as client:
            return await self.agent.run_task(self.context,client,model)

    async def test_loop_uses_intermediate_results_and_only_observed_citations(self):
        seen=[]
        async def model(messages,tools,max_tokens,timeout):
            seen.append(copy.deepcopy(messages))
            self.assertEqual({t["function"]["name"] for t in tools},{"search_documents","read_document_version"})
            self.assertLessEqual(max_tokens,1024); self.assertLessEqual(timeout,60)
            if len(seen)==1: return reply(calls=[call(arguments={"mode":"metadata","query":"missing"})])
            last=json.loads(messages[-1]["content"])
            if last.get("data",{}).get("items")==[]:
                return reply(calls=[call(arguments={"mode":"keyword","query":"deployment"},ident="call_2")])
            return reply(json.dumps({"answer":"Run documented checks.","citations":[CITATION]}))
        outcome=await self.run_model(model)
        self.assertEqual(outcome["state"],"completed"); self.assertEqual(self.calls,3)
        self.assertEqual(self.result["citations"],[CITATION])
        self.assertEqual(seen[0][1]["content"],"find public deployment instructions")
        self.assertNotIn(self.context["session_token"],json.dumps(seen))

    async def test_revocation_prevents_next_model_request_and_discards_old_context(self):
        seen=[]
        async def model(messages,**kwargs):
            seen.append(copy.deepcopy(messages)); self.denied=True
            return reply(calls=[call()])
        outcome=await self.run_model(model)
        self.assertEqual(outcome["state"],"failed"); self.assertIsNone(self.result)
        self.assertEqual(len(seen),1); self.assertEqual(self.calls,1)

    async def test_model_request_budget_stops_without_false_completion(self):
        async def model(messages,**kwargs): return reply(calls=[call(ident="call_"+str(len(messages)))])
        outcome=await self.run_model(model)
        self.assertEqual(outcome["state"],"failed"); self.assertEqual(self.calls,8)
        self.assertEqual(outcome["error_code"],"budget_exhausted"); self.assertIsNone(self.result)

    async def test_invalid_tool_is_not_executed_and_repeated_error_stops(self):
        async def model(messages,**kwargs): return reply(calls=[call("execute_archive_plan",{"approved":True},ident="call_"+str(len(messages)))])
        outcome=await self.run_model(model)
        self.assertEqual(outcome["error_code"],"repeated_tool_error")
        self.assertFalse(any("/tools/" in path for path,_ in self.requests))

    async def test_unknown_citation_is_rejected_before_result_submission(self):
        async def model(messages,**kwargs): return reply(json.dumps({"answer":"invented","citations":[CITATION]}))
        outcome=await self.run_model(model)
        self.assertEqual(outcome["error_code"],"invalid_source"); self.assertIsNone(self.result)

    async def test_timeout_and_truncated_model_output_are_failed_not_completed(self):
        async def timeout(messages,**kwargs): raise asyncio.TimeoutError()
        outcome=await self.run_model(timeout); self.assertEqual(outcome["error_code"],"timeout")
        async def truncated(messages,**kwargs): return reply("unfinished",finish="length")
        outcome=await self.run_model(truncated); self.assertEqual(outcome["error_code"],"model_invalid")

    async def test_project_debug_scope_never_reaches_runtime_or_model(self):
        self.context["scope"]="project_approved_versions"
        async def model(messages,**kwargs): self.fail("model must not run")
        async with create_connected_server_and_client_session(create_server(self.context)) as client:
            with self.assertRaises(ValueError): await self.agent.run_task(self.context,client,model)
        self.assertEqual(self.requests,[])

    async def test_lost_completion_response_never_submits_opposite_terminal_outcome(self):
        self.drop_completion=True
        async def model(messages,**kwargs): return reply(json.dumps({"answer":"No verified evidence yet.","citations":[]}))
        outcome=await self.run_model(model)
        self.assertEqual(outcome,{"state":"completed"})
        finishes=[body for path,body in self.requests if path.endswith("/run/finish")]
        self.assertTrue(1 <= len(finishes) <= 2)
        self.assertTrue(all(body==finishes[0] and body["status"]=="completed" for body in finishes))
        self.assertEqual(self.result["answer"],"No verified evidence yet.")

    async def test_uncertain_completion_replays_identical_outcome_before_read_can_overtake_commit(self):
        self.delay_completion=True
        async def model(messages,**kwargs): return reply(json.dumps({"answer":"No verified evidence yet.","citations":[]}))
        outcome=await self.run_model(model)
        self.assertEqual(outcome,{"state":"completed"})
        finishes=[body for path,body in self.requests if path.endswith("/run/finish")]
        self.assertEqual(len(finishes),2)
        self.assertEqual(finishes[0],finishes[1])
        self.assertEqual(self.state,"completed")

    async def test_twelve_tool_limit_stops_before_thirteenth_tool(self):
        async def model(messages,**kwargs):
            return reply(calls=[call(ident=f"call_{len(messages)}_{i}") for i in range(4)])
        outcome=await self.run_model(model)
        self.assertEqual(outcome["error_code"],"budget_exhausted")
        self.assertEqual(sum("/tools/" in path for path,_ in self.requests),12)
        self.assertEqual(self.calls,4); self.assertIsNone(self.result)

    async def test_authoritative_source_rejection_persists_failure_instead_of_stranding_run(self):
        self.reject_source=True
        async def model(messages,**kwargs): return reply(json.dumps({"answer":"No verified evidence yet.","citations":[]}))
        outcome=await self.run_model(model)
        self.assertEqual(outcome,{"state":"failed","error_code":"invalid_source"})
        finishes=[body for path,body in self.requests if path.endswith("/run/finish")]
        self.assertEqual([body["status"] for body in finishes],["completed","failed"])
        self.assertIsNone(self.result)

    async def test_oversized_history_stops_before_next_model_call(self):
        async def model(messages,**kwargs): return reply("x"*63000,calls=[call()])
        outcome=await self.run_model(model)
        self.assertEqual(outcome["error_code"],"budget_exhausted")
        self.assertEqual(self.calls,1); self.assertIsNone(self.result)
