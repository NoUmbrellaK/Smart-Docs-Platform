"""Fixed-destination, bounded DeepSeek transport; no model retries or content logging."""
import asyncio
from contextlib import contextmanager
import os
import re
import time
import uuid

import httpx
from .agent_runner import RunError, encoded
from .mcp_read import REPORT_TOOL
from .search_http import strict_json


MODEL = "deepseek-flash"
URL = "https://api.deepseek.com/chat/completions"


@contextmanager
def open_audit(path, task_id):
    """Create one private, exclusive task trace. Caller provides a protected parent directory."""
    if not isinstance(task_id,str) or not re.fullmatch("[0-9a-f]{32}",task_id):
        raise ValueError("invalid audit configuration")
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except OSError:
        raise ValueError("audit file unavailable") from None
    with os.fdopen(fd,"wb") as output:
        def record(event):
            raw=encoded({**event,"task_id":task_id,"time_unix_ms":int(time.time()*1000)})+b"\n"
            if len(raw)>4096: raise ValueError("invalid audit record")
            output.write(raw); output.flush(); os.fsync(output.fileno())
        yield record


def usage_counts(value):
    keys=("prompt_tokens","completion_tokens","total_tokens")
    if (not isinstance(value,dict) or any(type(value.get(k)) is not int or not 0<=value[k]<=2000000 for k in keys)
            or value["prompt_tokens"]+value["completion_tokens"]!=value["total_tokens"]):
        return None
    result={k:value[k] for k in keys}
    cache=("prompt_cache_hit_tokens","prompt_cache_miss_tokens")
    if (all(type(value.get(k)) is int and 0<=value[k]<=value["prompt_tokens"] for k in cache)
            and sum(value[k] for k in cache)==value["prompt_tokens"]):
        result.update({k:value[k] for k in cache})
    return result


class DeepSeekModel:
    """One instance per task. transport is a trusted-code HTTPX test seam, never user input."""
    def __init__(self, api_key, audit, *, max_calls=8, transport=None):
        if (not isinstance(api_key,str) or not re.fullmatch(r"[!-~]{1,256}",api_key)
                or not callable(audit) or type(max_calls) is not int or not 1<=max_calls<=8):
            raise ValueError("invalid provider configuration")
        self._key=api_key; self._audit=audit; self._transport=transport
        self._limit=max_calls; self._calls=0; self._active=False

    def record(self,event):
        try: self._audit(event)
        except (OSError,ValueError): raise RunError("dependency_failed") from None

    async def __call__(self, *, messages, tools, max_tokens, timeout):
        if self._active or self._calls>=self._limit: raise RunError("budget_exhausted")
        if (type(max_tokens) is not int or not 1<=max_tokens<=8192 or not isinstance(tools,list)
                or type(timeout) not in (int,float) or not 0<timeout<=60):
            raise RunError("model_invalid")
        report = any(isinstance(tool,dict) and isinstance(tool.get('function'),dict)
                     and tool['function'].get('name')==REPORT_TOOL for tool in tools)
        output_tokens = max_tokens if report else min(max_tokens,512)
        body=encoded({"model":MODEL,"messages":messages,"tools":tools,"stream":False,
            "thinking":{"type":"disabled"},"max_tokens":output_tokens,"temperature":0})
        if len(body)>65536: raise RunError("budget_exhausted")
        self._calls+=1; self._active=True
        request_id=uuid.uuid4().hex; start=time.monotonic(); usage=None; reason="provider_unavailable"
        base={"request_id":request_id,"sequence":self._calls,"model":MODEL}
        try:
            self.record({**base,"event":"model_attempt","request_bytes":len(body),"max_tokens":output_tokens})
            try:
                response=await asyncio.wait_for(self._send(body),timeout)
                usage=usage_counts(response.get("usage")); reason="success"
                return {"choices":response["choices"],"usage":usage}
            except asyncio.CancelledError:
                reason="cancelled"; raise
            except (asyncio.TimeoutError,httpx.TimeoutException):
                reason="timeout"; raise RunError("timeout") from None
            except RunError as error:
                reason=getattr(error,"provider_reason",error.code); raise
            except (httpx.HTTPError,ValueError,KeyError,TypeError,RecursionError):
                reason="provider_invalid"; raise RunError("dependency_failed") from None
            finally:
                event={**base,"event":"model_result","reason":reason,"usage":usage,
                       "duration_ms":int((time.monotonic()-start)*1000)}
                if reason=="cancelled":
                    try: self.record(event)
                    except RunError: pass
                else: self.record(event)
        finally:
            self._active=False

    async def _send(self,body):
        async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=15,
                limits=httpx.Limits(max_connections=1,max_keepalive_connections=0),transport=self._transport) as client:
            async with client.stream("POST",URL,content=body,headers={"Authorization":"Bearer "+self._key,
                    "Content-Type":"application/json","Accept-Encoding":"identity"}) as response:
                if response.status_code!=200:
                    error=RunError("dependency_failed")
                    error.provider_reason={401:"provider_auth",402:"provider_balance",429:"provider_rate_limit"}.get(
                        response.status_code,"provider_unavailable" if response.status_code>=500 else "provider_rejected")
                    raise error
                if response.headers.get("content-encoding","identity").lower()!="identity":
                    raise RunError("model_invalid")
                raw=bytearray()
                async for chunk in response.aiter_raw(chunk_size=8192):
                    if len(raw)+len(chunk)>65536: raise RunError("model_invalid")
                    raw.extend(chunk)
                data=strict_json(bytes(raw))
                if (not isinstance(data,dict) or not isinstance(data.get("choices"),list)
                        or len(data["choices"])!=1 or not isinstance(data["choices"][0],dict)
                        or not isinstance(data["choices"][0].get("message"),dict)):
                    raise RunError("model_invalid")
                choice=data["choices"][0]
                return {"choices":[{"finish_reason":choice.get("finish_reason"),
                    "message":{k:v for k,v in choice["message"].items() if k in {"role","content","tool_calls"}}}],
                    "usage":data.get("usage")}
