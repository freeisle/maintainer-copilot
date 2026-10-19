"""webhook 单测：HMAC 校验 / 幂等去重 / 事件路由(分诊图以假载荷替身)。"""
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maintainer_copilot import server
from maintainer_copilot.config import Settings

PAYLOAD = {
    "action": "opened",
    "issue": {"number": 42, "title": "测试问题", "body": "复现步骤"},
    "repository": {"full_name": "freeisle/ragent"},
}

FAKE_GATE = {"repo": "freeisle/ragent", "task_type": "triage", "draft": "草稿", "citations": [], "reflection": {"passed": True}}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: Settings(github_webhook_secret="s3cret", data_dir=tmp_path),
    )
    monkeypatch.setattr(server, "_STORE", None)  # 重置单例, 用 tmp 路径
    monkeypatch.setattr(server, "_GRAPH", None)
    async def fake_run(repo, title, body, number=None):
        return dict(FAKE_GATE), "triage-testid"

    monkeypatch.setattr(server, "run_triage_to_gate", fake_run)
    return TestClient(server.app)


def _sign(body: bytes, secret: str = "s3cret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_missing_signature_rejected(client: TestClient) -> None:
    r = client.post("/webhook", json=PAYLOAD, headers={"X-GitHub-Event": "issues"})
    assert r.status_code == 401


def test_valid_signature_and_routing(client: TestClient) -> None:
    body = json.dumps(PAYLOAD).encode()
    r = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d-1",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert r.status_code == 200
    assert r.json()["routed"] == "triage"
    # 后台任务入队(轮询等待)
    for _ in range(50):
        items = client.get("/api/pending").json()
        if items:
            break
        time.sleep(0.05)
    assert items and items[0]["draft"] == "草稿"


def test_duplicate_delivery_ignored(client: TestClient) -> None:
    body = json.dumps(PAYLOAD).encode()
    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "d-2",
        "X-Hub-Signature-256": _sign(body),
    }
    first = client.post("/webhook", content=body, headers=headers)
    second = client.post("/webhook", content=body, headers=headers)
    assert first.json()["routed"] == "triage"
    assert second.json().get("duplicate") is True


def test_unrelated_event_ignored(client: TestClient) -> None:
    body = json.dumps({"action": "created", "comment": {}}).encode()
    r = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-GitHub-Delivery": "d-3",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert r.json().get("ignored") == "issue_comment"

@pytest.mark.asyncio
async def test_parallel_approval_is_not_executed_twice(tmp_path, monkeypatch):
    import asyncio
    import httpx
    store=server.PendingStore(tmp_path/'pending.sqlite')
    store.add({'id':'one','thread_id':'thread','status':'pending'})
    monkeypatch.setattr(server,'_store',lambda:store)
    started,release=asyncio.Event(),asyncio.Event()
    class Graph:
        calls=0
        async def astream(self,*args,**kwargs):
            self.calls+=1; started.set(); await release.wait()
            yield {'executor':{'final_action':{'executed':True,'dry_run':True}}}
    graph=Graph(); monkeypatch.setattr(server,'_get_graph',lambda:graph)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(server.app),base_url='http://test') as c:
        first=asyncio.create_task(c.post('/api/approve/one',json={'decision':'approved'}))
        await started.wait()
        assert (await c.post('/api/approve/one',json={'decision':'approved'})).status_code==409
        release.set()
        assert (await first).status_code==200
        assert (await c.post('/api/approve/one',json={'decision':'approved'})).status_code==409
    assert graph.calls==1
