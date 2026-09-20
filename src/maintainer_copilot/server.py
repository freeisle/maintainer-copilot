"""FastAPI 服务：webhook 接收 + HITL 审核台 API + 静态页面。

- POST /api/triage: 触发分诊, 状态机跑到 HumanGate 中断, 草稿入待审队列
- POST /api/approve/{id}: 批准/编辑/驳回 -> Command(resume) 恢复执行 -> Executor 写操作
- webhook: HMAC-SHA256 签名校验(D8 补幂等去重与事件路由)
"""
import asyncio
import hmac
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

from .config import get_settings
from .graph.supervisor import build_graph

logger = logging.getLogger(__name__)

app = FastAPI(title="maintainer-copilot", version="0.1.0")

# HITL 待审队列(D7 内存; D8: SQLite 持久化)
_PENDING: dict[str, dict] = {}
_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def _extract_interrupt(event: dict) -> Any | None:
    inter = event.get("__interrupt__")
    if not inter:
        return None
    first = inter[0] if isinstance(inter, (list, tuple)) else inter
    return first.value if hasattr(first, "value") else first


class TriageRequest(BaseModel):
    repo: str
    title: str
    body: str = ""
    number: int | None = None


class ApproveRequest(BaseModel):
    decision: str  # approved | edited | rejected
    edited_draft: str | None = None


@app.get("/api/pending")
async def list_pending() -> list[dict]:
    return [{"id": k, **{f: v for f, v in item.items()}} for k, item in _PENDING.items()]


@app.post("/api/triage")
async def create_triage(req: TriageRequest) -> dict:
    item_id = uuid.uuid4().hex[:12]
    thread_id = f"triage-{item_id}"
    state = {
        "repo": req.repo,
        "task_type": "triage",
        "issue": {"title": req.title, "body": req.body, "number": req.number},
        "messages": [{"role": "user", "content": f"分诊: {req.title}"}],
    }
    config = {"configurable": {"thread_id": thread_id}}
    graph = _get_graph()
    payload = None
    async for event in graph.astream(state, config, stream_mode="updates"):
        payload = _extract_interrupt(event)
        if payload is not None:
            break
    if payload is None:
        # 未到达闸门(降级路径): 直接记录结果
        return {"ok": True, "id": item_id, "gate": False, "note": "degraded-or-completed"}
    _PENDING[item_id] = {
        "id": item_id,
        "thread_id": thread_id,
        "repo": payload.get("repo", ""),
        "task_type": payload.get("task_type", ""),
        "draft": payload.get("draft", ""),
        "citations": payload.get("citations", []),
        "reflection": payload.get("reflection", {}),
        "status": "pending",
    }
    return {"ok": True, "id": item_id, "gate": True, "payload": _PENDING[item_id]}


@app.post("/api/approve/{item_id}")
async def approve(item_id: str, req: ApproveRequest) -> dict:
    item = _PENDING.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="待审项不存在")
    decision = {"decision": req.decision, "edited_draft": req.edited_draft}
    graph = _get_graph()
    config = {"configurable": {"thread_id": item["thread_id"]}}
    final_action: dict = {}
    async for event in graph.astream(Command(resume=decision), config, stream_mode="updates"):
        if "executor" in event:
            final_action = event["executor"].get("final_action", {})
    item["status"] = req.decision
    item["final_action"] = final_action
    _PENDING[item_id] = item
    return {"ok": True, "id": item_id, "decision": req.decision, "final_action": final_action}


def _verify_signature(request: Request, body: bytes, secret: str) -> bool:
    if not secret:
        logger.warning("未配置 MC_GITHUB_WEBHOOK_SECRET, 跳过签名校验")
        return True
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), body, "sha256").hexdigest()
    return hmac.compare_digest(signature, expected)


@app.post("/webhook")
async def webhook(request: Request) -> JSONResponse:
    body = await request.body()
    if not _verify_signature(request, body, get_settings().github_webhook_secret):
        return JSONResponse({"ok": False, "error": "signature mismatch"}, status_code=401)
    # TODO(D8): X-GitHub-Delivery 幂等去重 + 事件路由(issues.opened -> /api/triage 逻辑)
    try:
        payload = json.loads(body)
        logger.info("webhook 事件: %s #%s", payload.get("action"), payload.get("issue", {}).get("number"))
    except ValueError:
        logger.warning("webhook body 非 JSON")
    return JSONResponse({"ok": True})


_static = Path(__file__).parent / "ui" / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="ui")
