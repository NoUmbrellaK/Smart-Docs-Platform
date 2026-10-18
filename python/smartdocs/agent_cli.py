"""Trusted local Agent entry; not a public server or a task-creation API."""
import argparse
import asyncio
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from .agent_runner import RunError, run_task
from .deepseek import DeepSeekModel, open_audit
from .mcp_read import load_context


async def run_session(context, client, model, audit):
    last_query=None; sequence=0
    class Tools:
        async def call_tool(self,name,arguments):
            nonlocal last_query,sequence
            start=time.monotonic(); sequence+=1
            base={"sequence":sequence,"tool":name}
            try: audit({**base,"event":"tool_attempt"})
            except (OSError,ValueError): raise RunError("dependency_failed") from None
            try:
                reply=await client.call_tool(name,arguments)
            except BaseException as error:
                status="cancelled" if isinstance(error,asyncio.CancelledError) else "timeout" if isinstance(error,asyncio.TimeoutError) else "dependency_failed"
                try: audit({**base,"event":"tool_result","status":status,"duration_ms":int((time.monotonic()-start)*1000)})
                except (OSError,ValueError): pass
                if isinstance(error,asyncio.CancelledError) or not isinstance(error,Exception): raise
                raise RunError(status) from None
            payload=reply.structuredContent if isinstance(reply.structuredContent,dict) else {}
            data=payload.get("data") if isinstance(payload.get("data"),dict) else {}
            status=payload.get("status")
            if status not in {"success","processing","unavailable","invalid_request","access_denied",
                              "conflict","busy","timeout","dependency_failed"}: status="dependency_failed"
            event={**base,"event":"tool_result","status":status,
                "duration_ms":int((time.monotonic()-start)*1000),
                "item_count":len(data["items"]) if isinstance(data.get("items"),list) else None,
                "chunk_count":len(data["chunks"]) if isinstance(data.get("chunks"),list) else int(isinstance(data.get("chunk"),dict)),
                "query_changed":False}
            if name=="search_documents":
                query=arguments["query"]
                event["query_changed"]=last_query is not None and query!=last_query; last_query=query
            try: audit(event)
            except (OSError,ValueError): raise RunError("dependency_failed") from None
            return reply
    return await run_task(context,Tools(),model)


def safe_outcome(outcome):
    if not isinstance(outcome,dict) or outcome.get("state") not in {"pending","running","completed","failed","cancelled","interrupted","unknown"}:
        return {"state":"unknown","error_code":"dependency_failed"}
    if outcome["state"]=="completed": return {"state":"completed"}
    code=outcome.get("error_code")
    if code not in {"timeout","budget_exhausted","model_invalid","invalid_source","dependency_failed",
                    "scope_changed","repeated_tool_error","access_denied","cancelled"}: code="dependency_failed"
    return {"state":outcome["state"],"error_code":code}


async def run_context(context_path, audit_path, api_key, max_calls=8, *, encoder_socket=None):
    context=load_context(context_path)
    if context["scope"]!="task_versions": raise ValueError("persistent task required")
    # Validate credentials before creating a trace or starting any child/claim.
    DeepSeekModel(api_key,lambda event:None,max_calls=max_calls)
    child_args=["-B","-m","smartdocs.mcp_read","--context",str(context_path)]
    if encoder_socket is not None: child_args.extend(["--encoder-socket",str(encoder_socket)])
    parameters=StdioServerParameters(command=sys.executable, args=child_args,
        env={"PATH":"/usr/bin:/bin","PYTHONPATH":str(Path(__file__).resolve().parents[1])})
    with open_audit(audit_path,context["task_id"]) as audit:
        model=DeepSeekModel(api_key,audit,max_calls=max_calls)
        audit({"event":"task_start"})
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams,read_timeout_seconds=timedelta(seconds=30)) as client:
                await client.initialize()
                outcome=await run_session(context,client,model,audit)
        outcome=safe_outcome(outcome)
        audit({"event":"task_outcome",**outcome})
        return outcome


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context",required=True)
    parser.add_argument("--audit",required=True)
    parser.add_argument("--max-model-calls",type=int,choices=range(1,9),default=8)
    parser.add_argument("--encoder-socket",help="optional trusted local query encoder socket")
    args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    # A secret must not be inherited by the MCP child. Never put it on the command line.
    key=os.environ.pop("DEEPSEEK_API_KEY","")
    try:
        outcome=asyncio.run(run_context(args.context,args.audit,key,args.max_model_calls,encoder_socket=args.encoder_socket))
    except ValueError:
        print(json.dumps({"state":"not_started","error_code":"configuration_error"})); return 2
    except (Exception,KeyboardInterrupt):
        print(json.dumps({"state":"unknown","error_code":"dependency_failed"})); return 1
    print(json.dumps(outcome))
    return 0 if outcome.get("state")=="completed" else 1


if __name__=="__main__":
    raise SystemExit(main())
