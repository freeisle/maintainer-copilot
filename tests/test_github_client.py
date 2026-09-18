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
