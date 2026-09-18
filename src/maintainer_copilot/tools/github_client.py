"""GitHub REST API 轻客户端：限流感知 + 重试 + 内存缓存。

设计要点:
- 每次响应检查 X-RateLimit-Remaining, 余量耗尽由调用方降级到本地索引
- 429 立即抛出 RateLimitExceeded(不重试, 避免雪上加霜); 5xx/网络错误指数退避重试
- 只实现本项目需要的 10 个端点, 不引第三方 SDK —— 限流细节可控, 面试有故事可讲
"""
import asyncio
import base64
import logging
import random
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class RateLimitExceeded(Exception):
    """限流余量耗尽, 调用方应降级到本地索引。"""


class GitHubClient:
    def __init__(self, token: str = "", timeout: float = 30.0) -> None:
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "maintainer-copilot",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout
        self._cache: dict[str, tuple[float, Any]] = {}  # url -> (expire_at, value)
        self._rate_remaining = 0

    # ---------- 通用请求 ----------

    async def _request(
        self, method: str, url: str, *, cache_ttl: float = 0.0, **kw: Any
    ) -> Any:
        if cache_ttl > 0:
            hit = self._cache.get(url)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            for attempt in range(4):  # 1 次正常 + 3 次重试
                try:
                    resp = await client.request(method, url, **kw)
                    self._sync_rate_limit(resp)
                    if resp.status_code == 429:
                        raise RateLimitExceeded(f"429 from {url}")
                    resp.raise_for_status()
                    data = resp.json() if resp.content else {}
                    if cache_ttl > 0:
                        self._cache[url] = (time.monotonic() + cache_ttl, data)
                    return data
                except RateLimitExceeded:
                    raise  # 不重试, 交给工具层降级
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code >= 500 and attempt < 3:
                        await asyncio.sleep(self._backoff(attempt))
                        continue
                    raise
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    if attempt < 3:
                        await asyncio.sleep(self._backoff(attempt))
                        continue
                    raise
        raise AssertionError("unreachable")

    def _sync_rate_limit(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            self._rate_remaining = int(remaining)

    @staticmethod
    def _backoff(attempt: int) -> float:
        """指数退避 + 抖动, 上限 8s。"""
        return min(2**attempt, 8) + random.uniform(0, 0.5)

    @property
    def rate_remaining(self) -> int:
        return self._rate_remaining

    # ---------- 读端点(进 LLM 工具列表) ----------

    async def get_issue(self, repo: str, number: int) -> dict:
        return await self._request("GET", f"{GITHUB_API}/repos/{repo}/issues/{number}", cache_ttl=300)

    async def list_comments(self, repo: str, number: int) -> list[dict]:
        return await self._request(
            "GET", f"{GITHUB_API}/repos/{repo}/issues/{number}/comments", cache_ttl=300
        )

    async def search_issues(self, repo: str, query: str, per_page: int = 30) -> dict:
        params = {"q": f"repo:{repo} {query}", "per_page": per_page}
        return await self._request("GET", f"{GITHUB_API}/search/issues", params=params)

    async def get_contents(self, repo: str, path: str, ref: str | None = None) -> str:
        params = {"ref": ref} if ref else None
        data = await self._request(
            "GET", f"{GITHUB_API}/repos/{repo}/contents/{path}", params=params, cache_ttl=3600
        )
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    async def list_dir(self, repo: str, path: str = "") -> list[dict]:
        url = f"{GITHUB_API}/repos/{repo}/contents/{path}" if path else f"{GITHUB_API}/repos/{repo}/contents"
        return await self._request("GET", url, cache_ttl=3600)

    async def get_commit_history(self, repo: str, path: str, per_page: int = 10) -> list[dict]:
        return await self._request(
            "GET", f"{GITHUB_API}/repos/{repo}/commits",
            params={"path": path, "per_page": per_page}, cache_ttl=600,
        )

    async def get_ci_status(self, repo: str, ref: str) -> list[dict]:
        return await self._request(
            "GET", f"{GITHUB_API}/repos/{repo}/actions/runs",
            params={"head_sha": ref}, cache_ttl=120,
        )

    async def search_code(self, repo: str, query: str) -> dict:
        return await self._request(
            "GET", f"{GITHUB_API}/search/code", params={"q": f"repo:{repo} {query}"}
        )

    # ---------- 写端点(仅 Executor 可用, 不进 LLM 工具列表) ----------

    async def add_comment(self, repo: str, number: int, body: str) -> dict:
        return await self._request(
            "POST", f"{GITHUB_API}/repos/{repo}/issues/{number}/comments", json={"body": body}
        )

    async def add_labels(self, repo: str, number: int, labels: list[str]) -> list[dict]:
        return await self._request(
            "POST", f"{GITHUB_API}/repos/{repo}/issues/{number}/labels", json={"labels": labels}
        )
