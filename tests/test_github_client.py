"""GitHub 客户端单测: 429 降级 / 5xx 重试 / 限流头解析。"""
import httpx
import pytest
import respx

from maintainer_copilot.tools.github_client import GitHubClient, RateLimitExceeded

URL = "https://api.github.com/repos/x/y/issues/1"


@pytest.mark.asyncio
async def test_429_raises_rate_limit() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(URL).mock(return_value=httpx.Response(429, headers={"X-RateLimit-Remaining": "0"}))
        client = GitHubClient(token="test")
        with pytest.raises(RateLimitExceeded):
            await client.get_issue("x/y", 1)


@pytest.mark.asyncio
async def test_retry_on_500_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GitHubClient, "_backoff", staticmethod(lambda attempt: 0.01))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(500)
        return httpx.Response(200, json={"number": 1, "title": "ok"})

    with respx.mock() as mock:
        mock.get(URL).mock(side_effect=handler)
        client = GitHubClient(token="test")
        data = await client.get_issue("x/y", 1)
    assert data["number"] == 1
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_rate_limit_header_tracking() -> None:
    with respx.mock() as mock:
        mock.get(URL).mock(
            return_value=httpx.Response(200, json={}, headers={"X-RateLimit-Remaining": "42"})
        )
        client = GitHubClient(token="test")
        await client.get_issue("x/y", 1)
        assert client.rate_remaining == 42


@pytest.mark.asyncio
async def test_cache_key_includes_params() -> None:
    """回归测试: 不同分页参数的请求不得互相命中缓存(曾导致分页死循环)。"""
    url = "https://api.github.com/repos/x/y/issues"
    with respx.mock() as mock:
        route = mock.get(url)
        route.side_effect = [
            httpx.Response(200, json=[{"number": 1}]),
            httpx.Response(200, json=[{"number": 2}]),
        ]
        client = GitHubClient(token="test")
        page1 = await client.list_issues("x/y", page=1, per_page=100)
        page2 = await client.list_issues("x/y", page=2, per_page=100)
    assert page1 == [{"number": 1}]
    assert page2 == [{"number": 2}]


DIFF_URL = "https://api.github.com/repos/x/y/pulls/1"


@pytest.mark.asyncio
async def test_get_pull_diff_returns_text() -> None:
    with respx.mock() as mock:
        mock.get(DIFF_URL).mock(return_value=httpx.Response(200, text="diff --git a/x b/x"))
        client = GitHubClient(token="test")
        diff = await client.get_pull_diff("x/y", 1)
    assert diff.startswith("diff --git")


@pytest.mark.asyncio
async def test_pull_json_and_diff_cache_not_colliding() -> None:
    """回归测试: 同一 PR URL 的 JSON 与 diff 请求仅 Accept 头不同, 缓存不得互串。"""
    with respx.mock() as mock:
        route = mock.get(DIFF_URL)
        route.side_effect = [
            httpx.Response(200, json={"number": 1, "title": "t"}),
            httpx.Response(200, text="diff --git a/x b/x"),
        ]
        client = GitHubClient(token="test")
        pr = await client.get_pull_request("x/y", 1)
        diff = await client.get_pull_diff("x/y", 1)
    assert pr["number"] == 1
    assert diff.startswith("diff --git")
