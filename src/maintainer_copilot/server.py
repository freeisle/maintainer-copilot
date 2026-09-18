"""FastAPI 服务：webhook 接收 + HITL 审核台 API + 静态页面。

- webhook: HMAC-SHA256 签名校验(Sprint 2 补幂等去重与事件路由入图)
- 审核台: 待审草稿队列, 批准/编辑/驳回
"""
import hmac
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import get_settings

logger = logging.getLogger(__name__)

app = FastAPI(title="maintainer-copilot", version="0.1.0")

# HITL 待审队列(脚手架: 内存; D8: SQLite 持久化 + 图恢复执行)
_PENDING: dict[str, dict] = {}


class ApproveRequest(BaseModel):
    decision: str  # approved | edited | rejected
    edited_draft: str | None = None


@app.get("/api/pending")
async def list_pending() -> list[dict]:
    return [{"id": k, **v} for k, v in _PENDING.items()]


@app.post("/api/approve/{item_id}")
async def approve(item_id: str, req: ApproveRequest) -> dict:
    # TODO(D8): 恢复挂起的图执行(Command(resume=...)), 触发 Executor
    item = _PENDING.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="待审项不存在")
    item["status"] = req.decision
    if req.decision == "edited" and req.edited_draft:
        item["final_draft"] = req.edited_draft
    _PENDING[item_id] = item
    return {"ok": True, "id": item_id, "decision": req.decision}


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
    # TODO(D8): X-GitHub-Delivery 幂等去重 + 事件路由入图(issues.opened -> triage)
    try:
        payload = json.loads(body)
        logger.info("webhook 事件: %s #%s", payload.get("action"), payload.get("issue", {}).get("number"))
    except ValueError:
        logger.warning("webhook body 非 JSON")
    return JSONResponse({"ok": True})


_static = Path(__file__).parent / "ui" / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="ui")
