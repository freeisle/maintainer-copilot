"""Opt-in, content-free OTLP/HTTP traces and numeric Langfuse scores.

Local JSONL is an audit sink, not a Langfuse emulator. Export is bounded and
best effort; telemetry failure must never change a workflow's outcome.
"""
import asyncio
from contextvars import ContextVar
from functools import wraps
import json
import logging
import math
from pathlib import Path
import threading
import time
import uuid

import httpx
from .config import get_settings

logger = logging.getLogger(__name__)
_parent = ContextVar("observation_parent", default=None)
_file_lock = threading.Lock()


def _append(path, record):
    with _file_lock:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _send(kind, payload, settings=None):
    settings = settings or get_settings()
    if not settings.observability_enabled:
        return
    try:
        await asyncio.to_thread(_append, settings.trace_path, {"kind":kind,"payload":payload})
        if settings.langfuse_url:
            if not settings.langfuse_public_key or not settings.langfuse_secret_key:
                raise ValueError("Langfuse keys required")
            endpoint = "/api/public/otel/v1/traces" if kind == "trace" else "/api/public/scores"
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.post(settings.langfuse_url.rstrip("/")+endpoint, json=payload,
                    auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
                    headers={"x-langfuse-ingestion-version":"4"})
                response.raise_for_status()
                if kind == "trace" and response.content:
                    partial = response.json().get("partialSuccess", {})
                    if int(partial.get("rejectedSpans", 0)):
                        raise ValueError("Collector rejected spans")
    except Exception as exc:
        # Never log response bodies, auth headers, exception messages or prompts.
        logger.warning("telemetry_export_failed kind=%s error=%s", kind, type(exc).__name__)


def span_payload(trace_id, span_id, parent, name, started, ended, failed=False, attributes=None, kind="span"):
    attrs = {"langfuse.observation.type":kind, "langfuse.trace.name":name,
             **(attributes or {})}
    span = {"traceId":trace_id,"spanId":span_id,"name":name,"kind":1,
            "startTimeUnixNano":str(started),"endTimeUnixNano":str(ended),
            "status":{"code":2 if failed else 1},
            "attributes":[{"key":k,"value":{"stringValue":str(v)}} for k,v in attrs.items()]}
    if parent:
        span["parentSpanId"] = parent
    return {"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"maintainer-copilot"}}]},
            "scopeSpans":[{"scope":{"name":"maintainer-copilot"},"spans":[span]}]}]}


def traced(name, kind="span"):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            settings = getattr(args[0], "settings", None) if args else None
            settings = settings or get_settings()
            if not settings.observability_enabled:
                return await fn(*args, **kwargs)
            parent = _parent.get()
            trace_id = parent[0] if parent else uuid.uuid4().hex
            span_id = uuid.uuid4().hex[:16]
            token = _parent.set((trace_id, span_id))
            started = time.time_ns()
            failed = False
            attrs = {}
            try:
                result = await fn(*args, **kwargs)
                if kind == "generation":
                    attrs = {"gen_ai.response.model":result.model, "mc.used_fallback":result.used_fallback,
                             "mc.fallback_reason":getattr(result,"fallback_reason","")}
                return result
            except BaseException as exc:
                failed = True
                attrs["error.type"] = type(exc).__name__
                raise
            finally:
                _parent.reset(token)
                await _send("trace", span_payload(trace_id,span_id,parent[1] if parent else None,
                    name,started,time.time_ns(),failed,attrs,kind), settings)
        return wrapped
    return decorate


async def publish_evaluation(suite, report, settings=None):
    """Publish top-level metrics against one trace, without questions/answers."""
    tid, sid = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    now = time.time_ns()
    await _send("trace", span_payload(tid,sid,None,"eval."+suite,now,now+1),settings)
    for name,value in report.items():
        if isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value):
            await _send("score", {"id":str(uuid.uuid5(uuid.NAMESPACE_URL,tid+name)),"traceId":tid,
                                 "name":name,"value":value,"dataType":"NUMERIC"},settings)
    return tid
