#!/usr/bin/env python3
"""Opt-in live model, only synthetic public content. Never run in default test discovery."""
import asyncio
import json
import logging
import os
import sys
import unittest

from m1_http_test import data
import m3_mcp_read_test as read_support
from smartdocs import worker
from smartdocs.agent_cli import run_context


LIVE_KEY=os.environ.pop("DEEPSEEK_API_KEY","") if os.environ.get("SMARTDOCS_LIVE_DEEPSEEK")=="1" else ""


@unittest.skipUnless(LIVE_KEY,"explicit live API opt-in required")
class DeepSeekE2E(read_support.McpReadE2E):
    __test__=False  # pytest discovery must never launch the paid opt-in script.
    def test_live_public_task(self):
        selected=self.upload("release-notes.txt",b"Public synthetic project: before deployment, create a backup named ORBIT-27 and verify its checksum.","text/plain")
        self.assertEqual(worker.run_once(self.client),"completed"); self.policy(selected,True)
        tasks=f'/api/v1/projects/{self.project["id"]}/agent-tasks'
        task=data(self.user.request("POST",tasks,{"question":
            "Find the deployment instructions. The old name may be legacy-guide: start by searching that name. "
            "If absent, discover other approved files. Read the actual text before answering, with exact chunk citations. "
            "This is public synthetic test data. Answer briefly in English.",
            "confirm_remote_ai":True,"versions":[{"file_id":selected["file_id"],"version_id":selected["version_id"]}]}),201)
        context={"backend_port":self.server.port,"origin":f"http://127.0.0.1:{self.server.port}",
            "session_token":next(c.value for c in self.user.cookies if c.name=="smartdocs_session"),
            "project_id":self.project["id"],"task_id":task["id"],"scope":"task_versions"}
        path=self.root/"context.json"; path.write_text(json.dumps(context)); path.chmod(0o600)
        audit_path=self.root/"provider.jsonl"
        outcome=asyncio.run(run_context(path,audit_path,LIVE_KEY,max_calls=6))
        events=[json.loads(line) for line in audit_path.read_text().splitlines()]
        # Safe metadata only; raw key, question, session, document text and model reasoning are never printed.
        print("LIVE_PROVIDER_TRACE "+json.dumps(events),flush=True)
        self.assertEqual(outcome["state"],"completed",outcome)
        saved=data(self.user.request("GET",tasks+"/"+task["id"]+"/run/result"))
        self.assertIn("ORBIT-27",saved["result"]["answer"])
        self.assertTrue(saved["result"]["citations"])
        self.assertTrue(all(c["version_id"]==selected["version_id"] for c in saved["result"]["citations"]))
        self.assertGreaterEqual(saved["model_calls"],3)
        self.assertLessEqual(saved["model_calls"],6)
        searches=[e for e in events if e["event"]=="tool_result" and e["tool"]=="search_documents"]
        self.assertGreaterEqual(len(searches),2)
        self.assertEqual(searches[0]["item_count"],0)
        self.assertTrue(any(e["query_changed"] and e["item_count"] for e in searches[1:]))
        print("LIVE_PUBLIC_TASK cited answer persisted; calls="+str(saved["model_calls"]),flush=True)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite([DeepSeekE2E("test_live_public_task")])


if __name__=="__main__":
    logging.disable(logging.CRITICAL)
    if os.environ.get("SMARTDOCS_LIVE_DEEPSEEK")!="1" or not LIVE_KEY:
        print("live test requires explicit opt-in and configured provider key"); raise SystemExit(2)
    suite=load_tests(None,None,None)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
