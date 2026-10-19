import json
import httpx
import pytest
import respx
from maintainer_copilot.rag.local import LocalRetriever
from maintainer_copilot.tools.github_client import GitHubClient
from maintainer_copilot.tools.github_tools import build_github_tools


@pytest.mark.asyncio
async def test_429_uses_real_local_index_once(tmp_path, caplog):
    path = tmp_path / "snapshot.jsonl"
    rows = [dict(repo="a/b", id=str(i), source="doc", path=p, text=t) for i,p,t in
            [(1,"install.md","install configuration"),(2,"x.md","other"),(3,"y.md","unrelated")]]
    path.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    registry = build_github_tools(GitHubClient(), LocalRetriever(path))
    with respx.mock() as mock:
        route = mock.get("https://api.github.com/repos/a/b/contents/install.md").respond(429)
        result = await registry.call("read_file", {"repo":"a/b", "path":"install.md"})
        assert route.call_count == 1
    assert result.ok and result.data["degraded"]
    assert result.data["docs"][0]["path"] == "install.md"
    assert "github_rate_limit" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401,403,500])
async def test_other_http_errors_never_fallback(status, monkeypatch):
    monkeypatch.setattr(GitHubClient, "_backoff", lambda *args: 0)
    registry = build_github_tools(GitHubClient())
    with respx.mock() as mock:
        route = mock.get("https://api.github.com/repos/a/b/issues/1").respond(status)
        result = await registry.call("get_issue", {"repo":"a/b","number":1})
        assert route.call_count == (4 if status == 500 else 1)
    assert not result.ok and result.error_kind == ("auth" if status in (401,403) else "service")


@pytest.mark.asyncio
async def test_missing_index_is_explicit(tmp_path):
    registry = build_github_tools(GitHubClient(), LocalRetriever(tmp_path / "missing"))
    with respx.mock() as mock:
        mock.get("https://api.github.com/repos/a/b/issues/1").respond(403, headers={"X-RateLimit-Remaining":"0"})
        result = await registry.call("get_issue", {"repo":"a/b","number":1})
    assert result.data["available"] is False and not result.data["docs"]


@pytest.mark.asyncio
async def test_timeout_no_fallback_or_outer_retry(monkeypatch):
    monkeypatch.setattr(GitHubClient, "_backoff", lambda *args: 0)
    with respx.mock() as mock:
        route = mock.get("https://api.github.com/repos/a/b/issues/1").mock(side_effect=httpx.ReadTimeout("timeout"))
        result = await build_github_tools(GitHubClient()).call("get_issue", {"repo":"a/b","number":1})
        assert route.call_count == 4
    assert not result.ok and result.error_kind == "timeout"


@pytest.mark.asyncio
async def test_review_rate_limit_returns_evidence_without_invented_diff(tmp_path, monkeypatch):
    from maintainer_copilot.config import get_settings
    from maintainer_copilot.workers.reviewer import ReviewWorker
    path = tmp_path / "snapshot.jsonl"
    path.write_text("\n".join(json.dumps(dict(repo="a/b", id=str(i), source="code", path=f"{i}.py", text=t))
                              for i,t in enumerate(["needle implementation", "other", "unrelated"])))
    monkeypatch.setattr(get_settings(), "local_index_path", path)
    with respx.mock() as mock:
        mock.get("https://api.github.com/repos/a/b/pulls/1", headers={"Accept":"application/vnd.github+json"}).respond(200,json={"title":"needle"})
        mock.get("https://api.github.com/repos/a/b/pulls/1", headers={"Accept":"application/vnd.github.diff"}).respond(429)
        result = await ReviewWorker(github=GitHubClient()).review("a/b", 1)
    assert result["pr_meta"]["degraded"] == "github_rate_limit"
    assert "暂不能作初审结论" in result["draft"]
    assert result["citations"] and "needle" in result["citations"][0]["snippet"]
