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
        self.receipt=True
        self.start_context=None
        self.report_receipt=None; self.drop_report=False; self.commit_report=True
        self.defer_report=False; self.reject_report_result=False
        self.report_operation='1'*32
        owner=self
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self): self.respond()
            def do_POST(self): self.respond()
            def do_PUT(self): self.respond()
            def respond(self):
                raw=self.rfile.read(int(self.headers.get("Content-Length","0")))
                body=json.loads(raw) if raw else None
                owner.requests.append((self.path,body))
                status=200; payload={}
                if self.path.endswith("/run/start"):
                    owner.state="running"; payload={"question":"find public deployment instructions","state":"running"}
                    if owner.start_context is not None: payload['handover_checklist']=owner.start_context
                    if owner.start_context is not None: payload['report_operation_id']=owner.report_operation
                elif self.path.endswith('/handover-report'):
                    payload={'id':'2'*32,'version':1,'url':self.path,'markdown_url':self.path+'/markdown'}
                    if owner.defer_report:
                        self.connection.shutdown(socket.SHUT_RDWR); self.connection.close()
                        owner.release_completion.wait(10)
                        owner.state='completed'; owner.report_receipt=payload
                        return
                    if owner.commit_report:
                        owner.state='completed'; owner.report_receipt=payload
                    if owner.drop_report:
                        self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
                elif self.path.endswith('/run/result') and owner.report_receipt:
                    payload={'state':'completed','kind':'handover_report','result':None,'error_code':None,
                             'model_calls':owner.calls,'handover_report':owner.report_receipt}
                    if owner.reject_report_result: status=503; payload={'code':'database_unavailable'}
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
                envelope={"data" if status==200 else "error":payload}
                if owner.receipt: envelope["request_id"]=f'{len(owner.requests):032x}'
                data=json.dumps(envelope).encode()
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

    async def test_malformed_checklist_stops_before_model_and_budget_admission(self):
        self.start_context={'revision':True,'items':[]}
        calls=[]
        async def model(**kwargs):
            calls.append(kwargs)
            return reply(json.dumps({'answer':'Unexpected','citations':[]}))
        self.assertEqual(await self.run_model(model),{'state':'failed','error_code':'dependency_failed'})
        self.assertEqual(calls,[])
        self.assertEqual(self.calls,0)

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
        async def model(messages,**kwargs): return reply(calls=[call("execute_archive_plan",
            {'plan_id':'1'*32,'revision':1,'operation_id':'2'*32},ident="call_"+str(len(messages)))])
        outcome=await self.run_model(model)
        self.assertEqual(outcome["error_code"],"repeated_tool_error")
        self.assertFalse(any("/tools/" in path or '/archive-plan' in path for path,_ in self.requests))

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

    async def test_provider_entry_records_dynamic_tool_summary_without_content(self):
        from smartdocs import agent_cli
        self.assertTrue(hasattr(agent_cli,"run_session"),"audited session missing")
        events=[]; count=0
        async def model(messages,**kwargs):
            nonlocal count
            count+=1
            if count==1: return reply(calls=[call(arguments={"mode":"metadata","query":"missing"})])
            if count==2: return reply(calls=[call(arguments={"mode":"keyword","query":"deployment"},ident="call_2")])
            return reply(json.dumps({"answer":"Run documented checks.","citations":[CITATION]}))
        async with create_connected_server_and_client_session(create_server(self.context)) as client:
            result=await agent_cli.run_session(self.context,client,model,events.append)
        self.assertEqual(result["state"],"completed")
        results=[e for e in events if e["event"]=="tool_result"]
        self.assertEqual([e["item_count"] for e in results],[0,1])
        self.assertFalse(results[0]["query_changed"]); self.assertTrue(results[1]["query_changed"])
        self.assertNotIn("deployment",json.dumps(events)); self.assertNotIn("Public",json.dumps(events))
        self.assertNotIn(self.context["session_token"],json.dumps(events))

    async def test_provider_tool_failure_has_attempt_and_terminal_summary(self):
        from smartdocs.agent_cli import run_session
        events=[]
        class Broken:
            async def call_tool(self,name,args): raise RuntimeError("private-protocol-error")
        async def model(**kwargs): return reply(calls=[call()])
        outcome=await run_session(self.context,Broken(),model,events.append)
        self.assertEqual(outcome,{"state":"failed","error_code":"dependency_failed"})
        self.assertEqual([e["event"] for e in events],["tool_attempt","tool_result"])
        self.assertEqual(events[-1]["status"],"dependency_failed")
        self.assertNotIn("private-protocol-error",json.dumps(events))

    async def test_model_admission_cumulates_receipts_even_without_final_citations(self):
        count=0
        async def model(messages, **kwargs):
            nonlocal count
            count+=1
            if count<3: return reply(calls=[call(ident=f'call_{count}')])
            return reply(json.dumps({"answer":"Some evidence remains uncertain.","citations":[]}))
        self.assertEqual(await self.run_model(model), {"state":"completed"})
        checks=[body for path,body in self.requests if path.endswith('/run/check')]
        receipts=[f'{index+1:032x}' for index,(path,_) in enumerate(self.requests) if '/tools/' in path]
        self.assertEqual(checks, [{"tool_request_ids":[]}, {"tool_request_ids":receipts[:1]},
                                 {"tool_request_ids":receipts}])

    async def test_missing_backend_receipt_prevents_next_model_send(self):
        self.receipt=False
        count=0
        async def model(messages, **kwargs):
            nonlocal count
            count+=1
            return reply(calls=[call(ident=f'call_{count}')])
        outcome=await self.run_model(model)
        self.assertEqual((outcome['error_code'],count), ('dependency_failed',1))

    def report_fixture(self):
        self.start_context={'revision':1,'items':[{'key':'deployment','title':'Deployment','description':'Check','required':True}]}
        return {'operation_id':self.report_operation,'report':{'summary':'Uncertain','limitations':'No evidence read',
            'recommendations':'Review manually','items':[{'key':'deployment','status':'uncertain','notes':'No evidence','citations':[]}]}}

    async def test_report_tool_is_terminal_and_never_finishes_with_plain_answer(self):
        arguments=self.report_fixture(); seen=[]
        async def model(messages,tools,**kwargs):
            seen.append(copy.deepcopy(messages))
            self.assertEqual({t['function']['name'] for t in tools},
                {'search_documents','read_document_version','save_handover_report'})
            return reply(calls=[call('save_handover_report',arguments)])
        outcome=await self.run_model(model)
        self.assertEqual(outcome.get('kind'),'handover_report')
        self.assertEqual(outcome.get('handover_report'),self.report_receipt)
        self.assertEqual(len(seen),1)
        self.assertEqual(sum(path.endswith('/handover-report') for path,_ in self.requests),1)
        self.assertFalse(any(path.endswith('/run/finish') for path,_ in self.requests))

    async def test_report_lost_ack_queries_authority_without_retrying_write(self):
        arguments=self.report_fixture(); self.drop_report=True
        async def model(**kwargs): return reply(calls=[call('save_handover_report',arguments)])
        outcome=await self.run_model(model)
        self.assertEqual((outcome.get('state'),outcome.get('kind')),('completed','handover_report'))
        self.assertEqual(sum(path.endswith('/handover-report') for path,_ in self.requests),1)
        self.assertFalse(any(path.endswith('/run/finish') for path,_ in self.requests))

    async def test_report_uncertain_write_never_races_failure_or_claims_completion(self):
        arguments=self.report_fixture(); self.drop_report=True; self.commit_report=False
        async def model(**kwargs): return reply(calls=[call('save_handover_report',arguments)])
        outcome=await self.run_model(model)
        self.assertEqual(outcome['state'],'unknown')
        self.assertEqual(self.state,'running')
        self.assertEqual(sum(path.endswith('/handover-report') for path,_ in self.requests),1)
        self.assertFalse(any(path.endswith('/run/finish') for path,_ in self.requests))

    async def test_report_cannot_share_batch_with_reads_or_more_writes(self):
        arguments=self.report_fixture()
        async def model(**kwargs): return reply(calls=[call('save_handover_report',arguments),call(ident='second')])
        outcome=await self.run_model(model)
        self.assertEqual(outcome['state'],'failed')
        self.assertFalse(any(path.endswith('/handover-report') or '/tools/' in path for path,_ in self.requests))

    async def test_report_missing_operation_and_plain_answer_cannot_claim_report_saved(self):
        self.report_fixture(); self.report_operation=None
        async def model(**kwargs): return reply(json.dumps({'answer':'Not a report','citations':[]}))
        self.assertEqual((await self.run_model(model)).get('error_code'),'dependency_failed')
        self.assertEqual(self.calls,0)
        self.report_operation='1'*32
        self.assertEqual((await self.run_model(model)).get('error_code'),'model_invalid')
        self.assertIsNone(self.report_receipt)

    async def test_report_multi_item_budget_reaches_real_provider_adapter(self):
        import httpx
        from smartdocs.deepseek import DeepSeekModel
        arguments=self.report_fixture()
        self.start_context['items']=[{'key':f'item_{i}','title':f'Material {i}','description':'Check','required':True} for i in range(6)]
        arguments['report']['items']=[{'key':f'item_{i}','status':'uncertain','notes':'Review the unavailable source before acceptance.',
            'citations':[]} for i in range(6)]
        requests=[]; events=[]
        async def receive(request):
            sent=json.loads(request.content); requests.append(sent)
            result=reply(calls=[call('save_handover_report',arguments)]) if sent['max_tokens']==2560 else reply('truncated',finish='length')
            return httpx.Response(200,stream=httpx.ByteStream(json.dumps(result).encode()))
        model=DeepSeekModel('test-secret',events.append,transport=httpx.MockTransport(receive))
        outcome=await self.run_model(model)
        self.assertEqual(outcome.get('kind'),'handover_report')
        self.assertEqual(requests[0]['max_tokens'],2560)
        self.assertEqual(events[0]['max_tokens'],2560)

    async def test_report_wrong_operation_is_not_dispatched(self):
        arguments=self.report_fixture(); arguments['operation_id']='9'*32
        async def model(messages,**kwargs): return reply(calls=[call('save_handover_report',arguments,ident=f'call_{len(messages)}')])
        outcome=await self.run_model(model)
        self.assertEqual(outcome['error_code'],'repeated_tool_error')
        self.assertFalse(any(path.endswith('/handover-report') for path,_ in self.requests))

    async def test_report_late_commit_after_unknown_is_not_overwritten(self):
        arguments=self.report_fixture(); self.defer_report=True
        async def model(**kwargs): return reply(calls=[call('save_handover_report',arguments)])
        self.assertEqual((await self.run_model(model))['state'],'unknown')
        self.assertEqual(self.state,'running')
        self.release_completion.set()
        for _ in range(50):
            if self.state=='completed': break
            await asyncio.sleep(0.01)
        outcome=await self.agent.RuntimeBackend(self.context).request('result')
        self.assertEqual((outcome['state'],outcome['kind']),('completed','handover_report'))
        self.assertFalse(any(path.endswith('/run/finish') for path,_ in self.requests))
        self.assertEqual(sum(path.endswith('/handover-report') for path,_ in self.requests),1)

    async def test_report_result_unavailable_is_unknown_not_false_failure(self):
        arguments=self.report_fixture(); self.reject_report_result=True
        async def model(**kwargs): return reply(calls=[call('save_handover_report',arguments)])
        self.assertEqual((await self.run_model(model))['state'],'unknown')
        self.assertEqual(self.state,'completed')
        self.assertFalse(any(path.endswith('/run/finish') for path,_ in self.requests))
