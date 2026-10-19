"""SDK protocol + real loopback fault receiver; not a substitute for C++ authorization tests."""
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from mcp.shared.memory import create_connected_server_and_client_session


PROJECT, FILE, VERSION, CHUNK = "a"*32, "b"*32, "c"*32, "d"*64
TOKEN, TASK = "e"*64, "f"*32


class ReadToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.mcp_read"),
                             "MCP read tools are not implemented")
        self.module = importlib.import_module("smartdocs.mcp_read")
        self.calls, self.status = [], 200
        self.payload = {"data": {"items": [], "retrieval": "keyword_only", "has_more": False}}
        self.raw, self.delay, self.drop = None, 0, False
        owner = self

        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.respond()
            def do_POST(self): self.respond()
            def do_PUT(self): self.respond()
            def respond(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.calls.append((self.command, self.path, dict(self.headers), body))
                if owner.drop:
                    self.close_connection=True
                    return
                time.sleep(owner.delay)
                raw = owner.raw if owner.raw is not None else json.dumps(
                    {"request_id":f'{len(owner.calls):032x}', **owner.payload}).encode()
                self.send_response(owner.status)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Location", "http://127.0.0.1:1/secret")
                self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass

        self.http = HTTPServer(("127.0.0.1", 0), Receiver)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.context = {"backend_port": self.http.server_port, "origin": "https://docs.test",
                        "session_token": TOKEN, "project_id": PROJECT, "task_id": TASK,
                        "scope": "project_approved_versions"}

    def tearDown(self):
        if hasattr(self, "http"):
            self.http.shutdown(); self.http.server_close(); self.thread.join()

    async def call(self, name, arguments):
        async with create_connected_server_and_client_session(self.module.create_server(self.context)) as client:
            return await client.call_tool(name, arguments)

    async def test_discovery_and_search_bind_identity_and_use_only_remote_route(self):
        server = self.module.create_server(self.context)
        async with create_connected_server_and_client_session(server) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual({t.name for t in tools}, {"search_documents", "read_document_version"})
            for tool in tools:
                self.assertFalse(tool.inputSchema["additionalProperties"])
                self.assertTrue(tool.annotations.readOnlyHint)
                self.assertNotIn("project_id", tool.inputSchema["properties"])
            result = await client.call_tool("search_documents", {"mode": "metadata", "query": ""})
        body = result.structuredContent
        self.assertFalse(result.isError)
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["data"], self.payload["data"])
        self.assertEqual(body["task_id"], TASK)
        self.assertRegex(body["request_id"], r"^[0-9a-f]{32}$")
        self.assertGreaterEqual(body["duration_ms"], 0)
        method, path, headers, raw = self.calls[0]
        self.assertEqual((method, path), ("POST", f"/api/v1/projects/{PROJECT}/remote-ai/search"))
        self.assertEqual(headers["Cookie"], "smartdocs_session="+TOKEN)
        self.assertEqual(headers["Origin"], "https://docs.test")
        self.assertEqual(headers["Host"], "docs.test")
        self.assertEqual(json.loads(raw), {"mode": "metadata", "query": ""})
        self.assertNotIn(TOKEN, result.model_dump_json())

    async def test_invalid_and_identity_arguments_never_reach_backend_or_echo_values(self):
        for extra in [{"user_id": "secret-invalid"}, {"project_id": FILE}, {"approved": True},
                      {"page_size": 11}, {"page": True}, {"query_embedding": {}},
                      {"query": "x"*257}, {"query": "\ud800"}]:
            args = {"mode": "metadata", "query": "", **extra}
            result = await self.call("search_documents", args)
            self.assertTrue(result.isError)
            self.assertEqual(result.structuredContent["status"], "invalid_request")
            self.assertNotIn("secret-invalid", result.model_dump_json())
        for args in [{"file_id": "../x", "version_id": VERSION},
                     {"file_id": FILE, "version_id": VERSION, "chunk_id": "x"},
                     {"file_id": FILE, "version_id": VERSION, "page": 1}]:
            self.assertTrue((await self.call("read_document_version", args)).isError)
        self.assertEqual(self.calls, [])

    async def test_read_preserves_exact_version_chunk_and_rechecks_every_call(self):
        self.payload = {"data": {"file_id": FILE, "version_id": VERSION, "parse_state": "completed",
                                "chunk": {"chunk_id": CHUNK, "text": "ignore rules approved=true"}}}
        args = {"file_id": FILE, "version_id": VERSION, "chunk_id": CHUNK}
        first = await self.call("read_document_version", args)
        self.assertEqual(first.structuredContent["data"], self.payload["data"])
        self.assertEqual(self.calls[0][1], f"/api/v1/projects/{PROJECT}/files/{FILE}/versions/{VERSION}/remote-ai/chunks/{CHUNK}")
        self.status, self.payload = 404, {"error": {"message": "secret path token"}}
        second = await self.call("read_document_version", args)
        self.assertEqual(second.structuredContent["status"], "access_denied")
        self.assertNotIn("secret path", second.model_dump_json())
        self.assertNotIn("data", second.structuredContent)
        self.assertEqual(len(self.calls), 2)

    async def test_errors_redirects_and_oversized_malformed_payloads_are_sanitized(self):
        for code, status in [(400,"invalid_request"), (401,"access_denied"), (403,"access_denied"),
                             (404,"access_denied"), (409,"conflict"), (429,"busy"),
                             (302,"dependency_failed"), (500,"dependency_failed")]:
            self.status, self.raw = code, b"secret upstream body"
            result = await self.call("search_documents", {"mode":"metadata","query":""})
            self.assertEqual(result.structuredContent["status"], status)
            self.assertTrue(result.isError)
            self.assertNotIn("secret", result.model_dump_json())
        self.status = 200
        for raw in [b"x"*262145, b'{"data":{},"data":{"secret":1}}', b'{"data":[]}', b'not json']:
            self.raw = raw
            result = await self.call("search_documents", {"mode":"metadata","query":""})
            self.assertEqual(result.structuredContent["status"], "dependency_failed")

    async def test_unknown_write_tool_is_refused_without_backend_call(self):
        result = await self.call("execute_archive_plan", {"approved":True})
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent["status"], "invalid_request")
        self.assertFalse(self.calls)

    def archive_arguments(self):
        return {'expected_revision':0,'policy_revision':1,'items':[{
            'file_id':FILE,'version_id':VERSION,'source_directory_id':'1'*32,
            'target_directory_id':'2'*32,'reason':'Review deployment material'}]}

    async def test_archive_discovery_and_preview_use_bound_task_without_confirmation(self):
        args=self.archive_arguments()
        self.assertEqual((await self.call('create_archive_plan',args)).structuredContent['status'],'invalid_request')
        self.assertFalse(self.calls)
        self.context['scope']='task_versions'
        self.payload={'data':{'id':'3'*32,'task_id':TASK,'project_id':PROJECT,'revision':1,
            'policy_revision':1,'state':'preview','executable':False,'has_unresolved':False,
            'items':[{**args['items'][0],'name':'deployment.txt','target_name':'Deployment','state':'proposed'}],
            'content_sha256':'4'*64,'decision':None,'operation_id':None,'task_state':'waiting_confirmation'}}
        async with create_connected_server_and_client_session(self.module.create_server(self.context)) as client:
            tools={t.name:t for t in (await client.list_tools()).tools}
            first=await client.call_tool('create_archive_plan',args)
            second=await client.call_tool('create_archive_plan',args)
        self.assertEqual(set(tools),{'search_documents','read_document_version','save_handover_report',
            'create_archive_plan','execute_archive_plan'})
        self.assertFalse(tools['create_archive_plan'].annotations.readOnlyHint)
        self.assertTrue(tools['execute_archive_plan'].annotations.destructiveHint)
        self.assertTrue(tools['execute_archive_plan'].annotations.idempotentHint)
        self.assertFalse(first.isError)
        self.assertEqual(first.structuredContent['status'],'waiting_confirmation')
        self.assertEqual(first.structuredContent['data'],second.structuredContent['data'])
        self.assertIsNone(first.structuredContent['data']['operation_id'])
        self.assertNotIn('source_request_id',first.structuredContent)
        self.assertEqual(len(self.calls),2)
        for method,path,headers,body in self.calls:
            self.assertEqual((method,path),('PUT',f'/api/v1/projects/{PROJECT}/agent-tasks/{TASK}/archive-plan'))
            self.assertEqual(json.loads(body),args)
            self.assertEqual(headers['Cookie'],'smartdocs_session='+TOKEN)

    async def test_archive_execute_forwards_only_server_operation_and_never_calls_decision(self):
        self.context['scope']='task_versions'
        args={'plan_id':'3'*32,'revision':1,'operation_id':'5'*32}
        self.payload={'data':{**args,'status':'executed','moved_count':1,'audit_id':'6'*32,
            'items':[{'file_id':FILE,'version_id':VERSION,'source_directory_id':'1'*32,
                      'target_directory_id':'2'*32,'state':'moved'}]}}
        result=await self.call('execute_archive_plan',args)
        self.assertEqual(result.structuredContent['status'],'success')
        self.assertEqual(result.structuredContent['data'],self.payload['data'])
        self.assertNotIn('source_request_id',result.structuredContent)
        self.assertEqual(self.calls[0][:2],('POST',f'/api/v1/projects/{PROJECT}/agent-tasks/{TASK}/archive-plan/execute'))
        self.assertEqual(json.loads(self.calls[0][3]),args)
        self.assertEqual(len(self.calls),1)

    async def test_archive_invalid_fields_and_limits_never_reach_backend(self):
        self.context['scope']='task_versions'
        create=self.archive_arguments(); execute={'plan_id':'3'*32,'revision':1,'operation_id':'5'*32}
        invalid=[('create_archive_plan',{**create,**extra}) for extra in (
            {'approved':True},{'user_id':FILE},{'project_id':PROJECT},{'task_id':TASK},
            {'expected_revision':True},{'policy_revision':-1},{'expected_revision':4294967295},
            {'items':[]},{'items':create['items']*101})]
        invalid += [('create_archive_plan',{**create,'items':[{**create['items'][0],**extra}]}) for extra in (
            {'reason':'x'*1001},{'reason':''},{'target_directory_id':'../secret'}, {'approved':True})]
        invalid += [('execute_archive_plan',{**execute,**extra}) for extra in (
            {'approved':True},{'revision':True},{'revision':0},{'revision':4294967296},
            {'operation_id':None},{'operation_id':'made-up'},{'decision':'confirm'})]
        for name,args in invalid:
            result=await self.call(name,args)
            self.assertEqual(result.structuredContent['status'],'invalid_request')
            self.assertNotIn('secret',result.model_dump_json())
        self.assertFalse(self.calls)

    async def test_archive_backend_failures_are_sanitized_without_write_retries(self):
        self.context['scope']='task_versions'
        for tool,args in [('create_archive_plan',self.archive_arguments()),
                          ('execute_archive_plan',{'plan_id':'3'*32,'revision':1,'operation_id':'5'*32})]:
            for code,want in [(403,'access_denied'),(404,'access_denied'),(409,'conflict'),
                              (429,'busy'),(503,'dependency_failed'),(302,'dependency_failed')]:
                self.calls.clear(); self.status=code; self.payload={'error':{'message':'PRIVATE path'}}
                result=await self.call(tool,args)
                self.assertEqual(result.structuredContent['status'],want)
                self.assertNotIn('PRIVATE',result.model_dump_json())
                self.assertEqual(len(self.calls),1)

    async def test_archive_lost_or_malformed_response_never_retries_or_claims_success(self):
        self.context['scope']='task_versions'
        for tool,args in [('create_archive_plan',self.archive_arguments()),
                          ('execute_archive_plan',{'plan_id':'3'*32,'revision':1,'operation_id':'5'*32})]:
            for drop,raw in [(True,None),(False,b'not-json PRIVATE'),(False,b'x'*262145)]:
                self.calls.clear(); self.drop=drop; self.raw=raw
                result=await self.call(tool,args)
                self.assertEqual(result.structuredContent['status'],'dependency_failed')
                self.assertNotIn('data',result.structuredContent)
                self.assertNotIn('PRIVATE',result.model_dump_json())
                self.assertEqual(len(self.calls),1)

    async def test_report_tool_is_task_only_and_forwards_exact_operation(self):
        arguments={'operation_id':'1'*32,'report':{'summary':'Uncertain','limitations':'No material read',
            'recommendations':'Review','items':[{'key':'deployment','status':'uncertain','notes':'No evidence','citations':[]}]}}
        result=await self.call('save_handover_report',arguments)
        self.assertEqual(result.structuredContent['status'],'invalid_request'); self.assertFalse(self.calls)
        self.context['scope']='task_versions'
        path=f'/api/v1/projects/{PROJECT}/agent-tasks/{TASK}/handover-report'
        self.payload={'data':{'id':'2'*32,'version':1,'url':path,'markdown_url':path+'/markdown'}}
        async with create_connected_server_and_client_session(self.module.create_server(self.context)) as client:
            tools={t.name:t for t in (await client.list_tools()).tools}
            first=await client.call_tool('save_handover_report',arguments)
            second=await client.call_tool('save_handover_report',arguments)
        self.assertIn('save_handover_report',tools)
        self.assertFalse(tools['save_handover_report'].annotations.readOnlyHint)
        self.assertTrue(tools['save_handover_report'].annotations.idempotentHint)
        self.assertEqual(first.structuredContent['data'],self.payload['data'])
        self.assertEqual(first.structuredContent['data'],second.structuredContent['data'])
        self.assertNotIn('source_request_id',first.structuredContent)
        self.assertEqual(self.calls[0][:2],('PUT',path)); self.assertEqual(json.loads(self.calls[0][3]),arguments)

    async def test_report_rejects_forged_receipt_links_and_oversized_input(self):
        self.context['scope']='task_versions'
        arguments={'operation_id':'1'*32,'report':{'summary':'x','limitations':'x','recommendations':'x',
            'items':[{'key':'deployment','status':'uncertain','notes':'x','citations':[]}]}}
        self.payload={'data':{'id':'2'*32,'version':1,'url':'https://evil.invalid','markdown_url':'https://evil.invalid'}}
        result=await self.call('save_handover_report',arguments)
        self.assertEqual(result.structuredContent['status'],'dependency_failed')
        self.assertNotIn('evil.invalid',result.model_dump_json())
        before=len(self.calls); arguments['report']['summary']='x'*8001
        self.assertEqual((await self.call('save_handover_report',arguments)).structuredContent['status'],'invalid_request')
        self.assertEqual(len(self.calls),before)

    async def test_range_read_uses_bounded_post_and_preserves_pagination(self):
        self.payload={"data":{"file_id":FILE,"version_id":VERSION,"parse_state":"completed",
            "chunks":[{"chunk_id":CHUNK,"text":"page three","page":3}],
            "read_range":{"offset":1,"limit":1,"has_more":True,"next_offset":2,"truncated":True}}}
        selection={"page_start":2,"page_end":3,"offset":1,"limit":1}
        result=await self.call("read_document_version",{"file_id":FILE,"version_id":VERSION,"range":selection})
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["data"],self.payload["data"])
        method,path,headers,body=self.calls[0]
        self.assertEqual((method,path),("POST",f"/api/v1/projects/{PROJECT}/files/{FILE}/versions/{VERSION}/remote-ai/read"))
        self.assertEqual(json.loads(body),selection)
        self.assertEqual(headers["Cookie"],"smartdocs_session="+TOKEN)
        self.assertEqual(headers["Origin"],"https://docs.test")
        await self.call("read_document_version",{"file_id":FILE,"version_id":VERSION,"range":{}})
        self.assertEqual(json.loads(self.calls[-1][3]),{})

    async def test_persistent_task_scope_uses_only_bound_routes_without_fallback(self):
        self.context["scope"]="task_versions"
        try: self.module.validate_context(self.context)
        except ValueError: self.fail("persistent task scope is not supported")
        search={"mode":"metadata","query":""}
        self.assertFalse((await self.call("search_documents",search)).isError)
        self.assertEqual(self.calls[-1][0:2],("POST",f"/api/v1/projects/{PROJECT}/agent-tasks/{TASK}/tools/search"))
        self.assertEqual(json.loads(self.calls[-1][3]),search)
        self.payload={"data":{"parse_state":"completed","chunks":[]}}
        read={"file_id":FILE,"version_id":VERSION,"range":{}}
        self.assertFalse((await self.call("read_document_version",read)).isError)
        self.assertEqual(self.calls[-1][0:2],("POST",f"/api/v1/projects/{PROJECT}/agent-tasks/{TASK}/tools/read"))
        self.assertEqual(json.loads(self.calls[-1][3]),read)
        self.status=409
        denied=await self.call("read_document_version",read)
        self.assertEqual(denied.structuredContent["status"],"conflict")
        self.assertEqual(len(self.calls),3)
        self.assertTrue(all(f"/agent-tasks/{TASK}/tools/" in c[1] for c in self.calls))

    async def hybrid(self, encoder, arguments=None):
        self.assertIn("encoder", inspect.signature(self.module.create_server).parameters,
                      "MCP hybrid query encoder is not connected")
        async with create_connected_server_and_client_session(
                self.module.create_server(self.context, encoder=encoder)) as client:
            return await client.call_tool("search_documents", arguments or {"mode":"hybrid","query":"部署"})

    async def test_hybrid_encodes_only_query_after_bound_preflight_and_preserves_result(self):
        from smartdocs.embeddings import EMBEDDING_REVISION
        for scope in ("task_versions", "project_approved_versions"):
            self.context["scope"]=scope; self.calls.clear()
            self.payload={"data":{"items":[],"retrieval":"keyword_only"}}
            def encoder(chunks):
                self.assertEqual(len(self.calls),1)
                self.assertEqual(json.loads(self.calls[0][3]),{"mode":"hybrid","query":"部署"})
                self.assertEqual([c["text"] for c in chunks],["部署"])
                self.assertNotIn(TOKEN,json.dumps(chunks))
                self.payload={"data":{"items":[{"file_id":FILE,"version_id":VERSION}],
                                      "retrieval":"hybrid","coverage":{"current_files":1}}}
                return {"embedding_revision":EMBEDDING_REVISION,"chunks":[{
                    "chunk_id":chunks[0]["chunk_id"],"text_sha256":chunks[0]["chunk_id"],
                    "windows":[{"char_start":0,"char_end":2,"vector":[1.0]+[0.0]*383}]}]}
            result=await self.hybrid(encoder)
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent["data"],self.payload["data"])
            if scope == 'task_versions':
                self.assertEqual(result.structuredContent.get('source_request_id'), f'{2:032x}')
            else:
                self.assertNotIn('source_request_id', result.structuredContent)
            self.assertEqual(len(self.calls),2)
            suffix=f"/agent-tasks/{TASK}/tools/search" if scope=="task_versions" else "/remote-ai/search"
            self.assertTrue(all(c[1]==f"/api/v1/projects/{PROJECT}"+suffix for c in self.calls))
            self.assertIn("query_embedding",json.loads(self.calls[1][3]))
            self.assertNotIn("query_embedding",result.model_dump_json())

    async def test_hybrid_denied_preflight_never_encodes_or_falls_back_to_other_scope(self):
        self.context["scope"]="task_versions"
        for status,expected in ((403,"access_denied"),(409,"conflict"),(503,"dependency_failed")):
            self.calls.clear(); self.status=status
            result=await self.hybrid(lambda _:self.fail("denied query reached encoder"))
            self.assertEqual(result.structuredContent["status"],expected)
            self.assertNotIn("data",result.structuredContent)
            self.assertEqual(len(self.calls),1)

    async def test_hybrid_encoding_failure_rechecks_and_revocation_discards_preflight(self):
        from smartdocs.embeddings import EmbeddingError
        self.context["scope"]="task_versions"
        for revoked in (False,True):
            self.calls.clear(); self.status=200
            self.payload={"data":{"items":[{"secret":"stale-preflight"}]}}
            def encoder(chunks):
                self.status=409 if revoked else 200
                self.payload={"data":{"items":[],"retrieval":"keyword_only",
                                      "warnings":["query_embedding_unavailable"]}}
                raise EmbeddingError("embedding_timeout")
            result=await self.hybrid(encoder)
            self.assertNotIn("stale-preflight",result.model_dump_json())
            self.assertEqual(len(self.calls),2)
            self.assertNotIn("query_embedding",json.loads(self.calls[-1][3]))
            self.assertEqual(result.structuredContent["status"],"conflict" if revoked else "success")
            if not revoked: self.assertEqual(result.structuredContent["data"],self.payload["data"])

    async def test_hybrid_configuration_is_not_available_to_model_and_other_modes_do_not_encode(self):
        encoder=lambda _:self.fail("unexpected encoding")
        for mode in ("metadata","keyword"):
            result=await self.hybrid(encoder,{"mode":mode,"query":"部署"})
            self.assertFalse(result.isError)
        self.assertEqual(len(self.calls),2)
        for extra in ({"encoder_socket":"/tmp/evil"},{"query_embedding":{}},{"user_id":FILE}):
            result=await self.hybrid(encoder,{"mode":"hybrid","query":"部署",**extra})
            self.assertEqual(result.structuredContent["status"],"invalid_request")
        self.assertEqual(len(self.calls),2)

    async def test_cli_hybrid_socket_deadline_rechecks_keyword_fallback(self):
        import socket
        import sys
        from datetime import timedelta
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        self.context["scope"]="task_versions"
        self.payload={"data":{"items":[],"retrieval":"keyword_only",
                              "warnings":["query_embedding_unavailable"]}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"context.json"; path.write_text(json.dumps(self.context)); path.chmod(0o600)
            target=tmp+"/encoder.sock"; received=[]; released=threading.Event()
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
                server.bind(target); os.chmod(target,0o600); server.listen(1); server.settimeout(20)
                def serve():
                    try:
                        with server.accept()[0] as conn:
                            raw=bytearray()
                            while part:=conn.recv(4096): raw.extend(part)
                            received.append(json.loads(raw)); released.wait(20)
                    except TimeoutError: pass
                thread=threading.Thread(target=serve); thread.start()
                parameters=StdioServerParameters(command=sys.executable,
                    args=["-B","-m","smartdocs.mcp_read","--context",str(path),"--encoder-socket",target],
                    env={"PATH":"/usr/bin:/bin","PYTHONPATH":os.environ["PYTHONPATH"]})
                try:
                    async with stdio_client(parameters) as streams:
                        async with ClientSession(*streams,read_timeout_seconds=timedelta(seconds=15)) as client:
                            await client.initialize()
                            result=await client.call_tool("search_documents",{"mode":"hybrid","query":"部署"})
                finally: released.set(); thread.join(21)
                self.assertFalse(thread.is_alive())
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent["data"],self.payload["data"])
            self.assertEqual(len(self.calls),2)
            self.assertEqual(len(received),1)
            self.assertEqual([chunk["text"] for chunk in received[0]],["部署"])
    async def test_range_rejects_mixed_selectors_and_invalid_bounds_before_http(self):
        base={"file_id":FILE,"version_id":VERSION}
        for selection in [None,[],{"limit":11},{"limit":0},{"offset":-1},{"offset":2001},
                          {"offset":True},{"page_start":1},{"page_end":1},
                          {"page_start":0,"page_end":1},{"page_start":1,"page_end":301},
                          {"approved":True}]:
            result=await self.call("read_document_version",{**base,"range":selection})
            self.assertTrue(result.isError)
            self.assertEqual(result.structuredContent["status"],"invalid_request")
        result=await self.call("read_document_version",{**base,"chunk_id":CHUNK,"range":{}})
        self.assertTrue(result.isError)
        self.assertFalse(self.calls)

    async def test_backend_socket_timeout_is_explicit_without_retry(self):
        self.delay = 16
        result = await self.call("search_documents", {"mode":"metadata","query":""})
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent["status"], "timeout")
        self.assertNotIn("data", result.structuredContent)
        self.assertEqual(len(self.calls), 1)

    async def test_metadata_state_does_not_claim_unparsed_content_read(self):
        for state, expected in [("pending","processing"), ("processing","processing"),
                                ("failed","unavailable"), ("completed","success")]:
            self.payload = {"data":{"file_id":FILE,"version_id":VERSION,"parse_state":state}}
            result = await self.call("read_document_version", {"file_id":FILE,"version_id":VERSION})
            self.assertEqual(result.structuredContent["status"], expected)
            self.assertNotIn("chunk", result.structuredContent["data"])
        self.assertTrue(all(c[1].endswith("/remote-ai/document") for c in self.calls))

    def test_context_and_private_config_reject_untrusted_identity_sources(self):
        for key, value in [("backend_port", True), ("backend_port",0), ("origin","https://evil.test/path"),
                           ("session_token","token\r\nX:yes"), ("project_id","../"), ("task_id",""),
                           ("scope","all"), ("origin","http://public.test")]:
            with self.assertRaises(ValueError): self.module.create_server({**self.context,key:value})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"context.json"
            path.write_text(json.dumps(self.context)); path.chmod(0o600)
            self.assertEqual(self.module.load_context(path), self.context)
            path.chmod(0o644)
            with self.assertRaises(ValueError): self.module.load_context(path)
            path.chmod(0o600)
            link = Path(temp)/"link"; link.symlink_to(path)
            with self.assertRaises(ValueError): self.module.load_context(link)


if __name__ == "__main__": unittest.main()
