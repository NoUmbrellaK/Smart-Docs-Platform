"""Provider boundary tests; HTTP transport replaces only the external service."""
import asyncio
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

import httpx
from smartdocs.agent_runner import RunError


RESPONSE = {"choices":[{"finish_reason":"stop","message":{"role":"assistant",
    "content":'{"answer":"Evidence insufficient.","citations":[]}',"reasoning_content":"hidden"}}],
    "usage":{"prompt_tokens":20,"completion_tokens":10,"total_tokens":30,
             "prompt_cache_hit_tokens":5,"prompt_cache_miss_tokens":15}}
ARGS = {"messages":[{"role":"user","content":"public fixture"}],"tools":[],"max_tokens":1024,"timeout":1}


def streamed(value):
    return httpx.Response(200,stream=httpx.ByteStream(json.dumps(value).encode()))


class DeepSeekTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("smartdocs.deepseek"),"provider transport missing")
        self.provider=importlib.import_module("smartdocs.deepseek")
        self.events=[]; self.requests=[]

    def model(self,handler=None,**kwargs):
        async def receive(request):
            self.requests.append(request)
            return await handler(request) if handler else streamed(RESPONSE)
        return self.provider.DeepSeekModel("test-secret",self.events.append,
            transport=httpx.MockTransport(receive),**kwargs)

    async def test_fixed_destination_limits_hidden_reasoning_and_safe_usage(self):
        model=self.model(); result=await model(**ARGS)
        request=self.requests[0]; body=json.loads(request.content)
        self.assertEqual(str(request.url),"https://api.deepseek.com/chat/completions")
        self.assertEqual(request.headers["authorization"],"Bearer test-secret")
        self.assertNotIn("test-secret",request.content.decode())
        self.assertEqual(body["thinking"],{"type":"disabled"})
        self.assertFalse(body["stream"]); self.assertLessEqual(body["max_tokens"],512)
        self.assertNotIn("reasoning_content",result["choices"][0]["message"])
        self.assertEqual([e["event"] for e in self.events],["model_attempt","model_result"])
        self.assertEqual(self.events[-1]["usage"],RESPONSE["usage"])
        self.assertNotIn("public fixture",json.dumps(self.events)); self.assertNotIn("test-secret",json.dumps(self.events))

    async def test_http_rejections_are_not_retried_or_leaked(self):
        for status,reason in [(302,"provider_rejected"),(401,"provider_auth"),(402,"provider_balance"),
                              (429,"provider_rate_limit"),(503,"provider_unavailable")]:
            with self.subTest(status=status):
                async def reject(request): return httpx.Response(status,headers={"location":"https://evil.invalid"},text="secret-provider-body")
                model=self.model(reject)
                with self.assertRaises(RunError) as caught: await model(**ARGS)
                self.assertEqual(caught.exception.code,reason)
                self.assertEqual(self.events[-1]["reason"],reason)
                self.assertNotIn("secret-provider-body",str(caught.exception))
        self.assertEqual(len(self.requests),5)

    async def test_json_output_constraint_is_sent_only_for_read_answers(self):
        # Missing response_format leaves the real provider in its default text mode.
        for names, expected in [
            (['search_documents','read_document_version'], {'type':'json_object'}),
            (['search_documents','read_document_version','save_handover_report'], None),
            (['search_documents','read_document_version','create_archive_plan'], None),
        ]:
            with self.subTest(names=names):
                tools=[{'type':'function','function':{'name':name,'parameters':{'type':'object'}}} for name in names]
                await self.model()(**{**ARGS,'tools':tools,'max_tokens':1280})
                body=json.loads(self.requests[-1].content)
                self.assertEqual(body.get('response_format'),expected)
                self.assertEqual(body['tools'],tools)
                self.assertEqual(body['max_tokens'],512 if expected else 1280)
                self.assertEqual(body['thinking'],{'type':'disabled'})

    async def test_whole_request_deadline_cancels_transport(self):
        cancelled=asyncio.Event()
        async def slow(request):
            try: await asyncio.sleep(30)
            finally: cancelled.set()
        with self.assertRaises(RunError) as caught:
            await self.model(slow)(**{**ARGS,"timeout":0.02})
        self.assertEqual(caught.exception.code,"timeout"); self.assertTrue(cancelled.is_set())
        self.assertEqual(self.events[-1]["reason"],"timeout")

    async def test_external_cancellation_is_propagated_and_recorded(self):
        started=asyncio.Event()
        async def slow(request): started.set(); await asyncio.sleep(30)
        task=asyncio.create_task(self.model(slow)(**ARGS)); await started.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.events[-1]["reason"],"cancelled")

    async def test_stream_response_cap_and_compression_rejection_close_response(self):
        class Stream(httpx.AsyncByteStream):
            closed=False
            async def __aiter__(self):
                for _ in range(20): yield b" "*8192
            async def aclose(self): self.closed=True
        stream=Stream()
        async def large(request): return httpx.Response(200,stream=stream)
        with self.assertRaises(RunError): await self.model(large)(**ARGS)
        self.assertTrue(stream.closed)
        async def compressed(request): return httpx.Response(200,headers={"content-encoding":"gzip"},content=b"bad")
        with self.assertRaises(RunError): await self.model(compressed)(**ARGS)

    async def test_request_and_call_caps_block_before_network(self):
        model=self.model(max_calls=1)
        await model(**ARGS)
        with self.assertRaises(RunError) as caught: await model(**ARGS)
        self.assertEqual(caught.exception.code,"budget_exhausted"); self.assertEqual(len(self.requests),1)
        with self.assertRaises(RunError):
            await self.model()(**{**ARGS,"messages":[{"role":"user","content":"x"*65536}]})
        self.assertEqual(len(self.requests),1)

    async def test_unknown_or_invalid_usage_never_becomes_zero_cost(self):
        for usage in [None,{"prompt_tokens":True,"completion_tokens":1,"total_tokens":2},
                      {"prompt_tokens":20,"completion_tokens":10,"total_tokens":5}]:
            async def missing(request): return streamed({**RESPONSE,"usage":usage})
            await self.model(missing)(**ARGS)
            self.assertIsNone(self.events[-1]["usage"])

    async def test_malformed_response_is_safe_and_audit_failure_prevents_send(self):
        async def malformed(request): return httpx.Response(200,stream=httpx.ByteStream(b'{"choices":[],"choices":[]}'))
        with self.assertRaises(RunError): await self.model(malformed)(**ARGS)
        def fail_audit(event): raise OSError("private-path")
        attempted=[]
        async def watched(request): attempted.append(request); return streamed(RESPONSE)
        model=self.provider.DeepSeekModel("test-secret",fail_audit,transport=httpx.MockTransport(watched))
        with self.assertRaises(RunError) as caught: await model(**ARGS)
        self.assertEqual(caught.exception.code,"dependency_failed")
        self.assertNotIn("private-path",str(caught.exception)); self.assertEqual(len(self.requests),1)
        self.assertEqual(attempted,[])

    async def test_private_audit_is_exclusive_durable_and_task_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"trace.jsonl"
            with self.provider.open_audit(path,"a"*32) as record:
                model=self.provider.DeepSeekModel("test-secret",record,
                    transport=httpx.MockTransport(lambda request:streamed(RESPONSE)))
                await model(**ARGS)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            events=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(events),2); self.assertTrue(all(e["task_id"]=="a"*32 for e in events))
            original=path.read_bytes()
            with self.assertRaises(ValueError):
                with self.provider.open_audit(path,"a"*32): pass
            self.assertEqual(path.read_bytes(),original)
            link=Path(folder)/"link"; link.symlink_to(path)
            with self.assertRaises(ValueError):
                with self.provider.open_audit(link,"a"*32): pass

    async def test_invalid_credentials_and_configuration_never_reach_transport(self):
        for key in ["", "a\nb", "x"*257]:
            with self.assertRaises(ValueError): self.provider.DeepSeekModel(key,self.events.append)
        for count in [0,9,True]:
            with self.assertRaises(ValueError): self.model(max_calls=count)
        self.assertEqual(self.requests,[])
