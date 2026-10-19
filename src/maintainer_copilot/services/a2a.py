"""A2A 0.3 JSON-RPC read-only retrieval agent; no write tools or approvals.

Implements message/send, tasks/get and tasks/cancel for short synchronous tasks.
Completed tasks are retained in a bounded process-local cache (not durable jobs).
"""
import asyncio
import hmac
import uuid
from collections import OrderedDict

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from ..config import get_settings


class Search(BaseModel):
    repo: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=8, ge=1, le=20)


def create_app(backend=None, *, allowed_repos=None, token=None, url=None, timeout=20):
    from ..rag.federated import configured_retriever
    settings = get_settings()
    backend = backend or configured_retriever(allow_remote=False)
    allowed = set(allowed_repos if allowed_repos is not None else settings.a2a_allowed_repos)
    token = settings.a2a_token if token is None else token
    url = url or settings.a2a_public_url
    app = FastAPI(title="Maintainer knowledge A2A agent")
    tasks = OrderedDict()
    capacity = asyncio.Semaphore(4)

    def auth(request):
        if token and not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise HTTPException(401, "Unauthorized")

    @app.get("/.well-known/agent-card.json")
    async def card():
        result = {"name":"Maintainer knowledge", "description":"Repository-grounded read-only retrieval",
                  "version":"0.1.0", "protocolVersion":"0.3.0", "url":url, "preferredTransport":"JSONRPC",
                  "capabilities":{"streaming":False,"pushNotifications":False},
                  "defaultInputModes":["application/json"], "defaultOutputModes":["application/json"],
                  "skills":[{"id":"retrieve","name":"Retrieve repository evidence", "description":"DataPart: repo/query/top_k",
                             "tags":["retrieval","read-only"]}]}
        if token:
            result.update(securitySchemes={"bearer":{"type":"http","scheme":"bearer"}},security=[{"bearer":[]}])
        return result

    @app.post("/a2a")
    async def rpc(request: Request):
        auth(request)
        rid = None
        def error(code, message):
            return {"jsonrpc":"2.0","id":rid,"error":{"code":code,"message":message}}
        try:
            data = await request.json()
        except ValueError:
            return error(-32700,"Parse error")
        if not isinstance(data,dict) or data.get("jsonrpc") != "2.0" or "id" not in data:
            return error(-32600,"Invalid Request")
        rid = data["id"]
        params = data.get("params", {})
        if not isinstance(params,dict):
            return error(-32602,"Invalid params")
        method = data.get("method")
        if method in ("tasks/get","tasks/cancel"):
            task = tasks.get(params.get("id"))
            if task is None:
                return error(-32001,"Task not found")
            if method == "tasks/cancel":
                return error(-32002,"Completed task cannot be canceled")
            return {"jsonrpc":"2.0","id":rid,"result":task}
        if method != "message/send":
            return error(-32601,"Method not found")
        try:
            message = params["message"]
            if message.get("role") != "user" or not message.get("messageId"):
                raise ValueError("User messageId required")
            parts = message["parts"]
            if len(parts) != 1 or parts[0].get("kind") != "data":
                raise ValueError("One DataPart required")
            search = Search.model_validate(parts[0]["data"])
        except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
            return error(-32602,"Expected user Message with repo/query/top_k DataPart")
        if search.repo not in allowed:
            raise HTTPException(403,"Repository not allowed")
        try:
            async def retrieve():
                async with capacity:
                    return await backend.retrieve(search.query,search.repo,top_k=search.top_k)
            docs = await asyncio.wait_for(retrieve(), timeout=timeout)
        except TimeoutError:
            return error(-32603,"Retrieval timeout")
        except (OSError, ValueError, RuntimeError):
            return error(-32603,"Retrieval unavailable")
        task_id = uuid.uuid4().hex
        task = {"id":task_id,"contextId":message.get("contextId") or uuid.uuid4().hex,"kind":"task",
                "status":{"state":"completed"},
                "artifacts":[{"artifactId":uuid.uuid4().hex,"name":"evidence", "parts":[{"kind":"data","data":{"docs":docs}}]}]}
        tasks[task_id] = task
        while len(tasks) > 128:
            tasks.popitem(last=False)
        return {"jsonrpc":"2.0","id":rid,"result":task}

    return app
