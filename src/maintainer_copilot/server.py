"""FastAPI 服务：webhook 接收 + HITL 审核台 API + 静态页面。

- POST /api/triage: 触发分诊, 状态机跑到 HumanGate 中断, 草稿入待审队列(SQLite 持久化)
- POST /api/approve/{id}: 批准/编辑/驳回 -> Command(resume) 恢复执行 -> Executor 写操作
- POST /webhook: HMAC-SHA256 签名校验 + X-GitHub-Delivery 幂等去重;
  issues.opened 事件路由入分诊图(后台任务)
"""
import asyncio
import hmac
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PendingStore:
    """HITL 待审队列 + webhook 事件去重, SQLite 持久化(服务重启不丢)。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS pending (
                    id TEXT PRIMARY KEY, thread_id TEXT, repo TEXT, task_type TEXT,
                    draft TEXT, citations TEXT, reflection TEXT, status TEXT,
                    final_action TEXT, created_at TEXT, updated_at TEXT)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS webhook_events (
                    delivery TEXT PRIMARY KEY, event TEXT, repo TEXT, created_at TEXT)"""
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, item: dict) -> None:
        now = _now()
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pending VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"],
                    item.get("thread_id", ""),
                    item.get("repo", ""),
                    item.get("task_type", ""),
                    item.get("draft", ""),
                    json.dumps(item.get("citations") or [], ensure_ascii=False),
                    json.dumps(item.get("reflection") or {}, ensure_ascii=False),
                    item.get("status", "pending"),
                    json.dumps(item.get("final_action"), ensure_ascii=False)
                    if item.get("final_action")
                    else None,
                    now,
                    now,
                ),
            )

    def list(self) -> list[dict]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM pending ORDER BY created_at DESC").fetchall()
        return [self._row_to_item(r) for r in rows]

    def get(self, item_id: str) -> dict | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM pending WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def update_status(self, item_id: str, status: str, final_action: dict | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE pending SET status = ?, final_action = ?, updated_at = ? WHERE id = ?",
                (
                    status,
                    json.dumps(final_action, ensure_ascii=False) if final_action else None,
                    _now(),
                    item_id,
                ),
            )

    def mark_delivery(self, delivery: str, event: str, repo: str) -> bool:
        """记录 webhook 事件; 已存在返回 False(重复事件)。"""
        with self._lock, self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO webhook_events VALUES (?,?,?,?)",
                    (delivery, event, repo, _now()),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "repo": row["repo"],
            "task_type": row["task_type"],
            "draft": row["draft"],
            "citations": json.loads(row["citations"] or "[]"),
            "reflection": json.loads(row["reflection"] or "{}"),
            "status": row["status"],
            "final_action": json.loads(row["final_action"]) if row["final_action"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


_STORE: PendingStore | None = None
_GRAPH = None
_BG_LOOP: asyncio.AbstractEventLoop | None = None


def _submit_background(coro) -> None:
    """在独立线程的事件循环上执行后台任务。

    webhook 需快速响应; create_task 在 TestClient 的请求级任务组里会被取消,
    独立线程 + 专属事件循环在 uvicorn 与测试环境下都稳定。
    """
    global _BG_LOOP
    if _BG_LOOP is None or _BG_LOOP.is_closed():
        loop = asyncio.new_event_loop()
        _BG_LOOP = loop
        threading.Thread(target=loop.run_forever, daemon=True, name="bg-triage").start()
    asyncio.run_coroutine_threadsafe(coro, _BG_LOOP)


def _store() -> PendingStore:
    global _STORE
    if _STORE is None:
        _STORE = PendingStore(get_settings().data_dir / "pending.sqlite")
    return _STORE


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


async def run_triage_to_gate(
    repo: str, title: str, body: str, number: int | None = None
) -> tuple[dict | None, str]:
    """跑分诊图至 HumanGate 中断; 返回 (闸门载荷或 None, thread_id)。"""
    thread_id = f"triage-{uuid.uuid4().hex[:12]}"
    state = {
        "repo": repo,
        "task_type": "triage",
        "issue": {"title": title, "body": body, "number": number},
        "messages": [{"role": "user", "content": f"分诊: {title}"}],
    }
    config = {"configurable": {"thread_id": thread_id}}
    payload = None
    async for event in _get_graph().astream(state, config, stream_mode="updates"):
        payload = _extract_interrupt(event)
        if payload is not None:
            break
    return payload, thread_id


async def _triage_background(repo: str, title: str, body: str, number: int | None) -> None:
    try:
        payload, thread_id = await run_triage_to_gate(repo, title, body, number)
    except Exception:  # noqa: BLE001 - 后台任务异常只记录, 不影响 webhook 响应
        logger.exception("后台分诊异常: %s", title)
        return
    if payload is None:
        logger.info("分诊未达闸门(降级), 不入队: %s", title)
        return
    _store().add(
        {
            "id": thread_id.removeprefix("triage-"),
            "thread_id": thread_id,
            "repo": payload.get("repo", repo),
            "task_type": payload.get("task_type", "triage"),
            "draft": payload.get("draft", ""),
            "citations": payload.get("citations", []),
            "reflection": payload.get("reflection", {}),
            "status": "pending",
        }
    )


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
    return _store().list()


@app.post("/api/triage")
async def create_triage(req: TriageRequest) -> dict:
    payload, thread_id = await run_triage_to_gate(req.repo, req.title, req.body, req.number)
    if payload is None:
        return {"ok": True, "gate": False, "note": "degraded-or-completed"}
    item_id = thread_id.removeprefix("triage-")
    item = {
        "id": item_id,
        "thread_id": thread_id,
        "repo": payload.get("repo", ""),
        "task_type": payload.get("task_type", ""),
        "draft": payload.get("draft", ""),
        "citations": payload.get("citations", []),
        "reflection": payload.get("reflection", {}),
        "status": "pending",
    }
    _store().add(item)
    return {"ok": True, "id": item_id, "gate": True, "payload": item}


@app.post("/api/approve/{item_id}")
async def approve(item_id: str, req: ApproveRequest) -> dict:
    item = _store().get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="待审项不存在")
    decision = {"decision": req.decision, "edited_draft": req.edited_draft}
    config = {"configurable": {"thread_id": item["thread_id"]}}
    final_action: dict = {}
    async for event in _get_graph().astream(Command(resume=decision), config, stream_mode="updates"):
        if "executor" in event:
            final_action = event["executor"].get("final_action", {})
    _store().update_status(item_id, req.decision, final_action)
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
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad json"}, status_code=400)
    event = request.headers.get("X-GitHub-Event", "")
    repo = payload.get("repository", {}).get("full_name", "")
    # 幂等去重: 同一 delivery 只处理一次(webhook 可能重放)
    delivery = request.headers.get("X-GitHub-Delivery", "")
    if delivery and not _store().mark_delivery(delivery, event, repo):
        logger.info("重复 webhook 事件, 忽略: %s", delivery)
        return JSONResponse({"ok": True, "duplicate": True})
    if event == "issues" and payload.get("action") == "opened":
        issue = payload.get("issue", {})
        logger.info("webhook issues.opened: %s#%s -> 路由分诊", repo, issue.get("number"))
        _submit_background(
            _triage_background(repo, issue.get("title", ""), issue.get("body", "") or "", issue.get("number"))
        )
        return JSONResponse({"ok": True, "routed": "triage"})
    return JSONResponse({"ok": True, "ignored": event})


_static = Path(__file__).parent / "ui" / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="ui")
