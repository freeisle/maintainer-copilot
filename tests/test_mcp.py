"""MCP Server 单测：内存客户端 tools/list 与 tools/call(注入假 GitHub 客户端, 无网络)。"""
import pytest

from maintainer_copilot.tools import github_tools
from maintainer_copilot.tools.mcp_server import build_mcp_server


class FakeGitHubClient:
    async def get_issue(self, repo: str, number: int) -> dict:
        return {"number": number, "title": f"fake-{repo}-{number}"}

    async def search_issues(self, repo: str, query: str, per_page: int = 30) -> dict:
        return {"total_count": 0, "items": []}

    async def get_contents(self, repo: str, path: str, ref: str | None = None) -> str:
        return "fake file content"


@pytest.mark.asyncio
async def test_mcp_list_tools_and_call() -> None:
    registry = github_tools.build_github_tools(client=FakeGitHubClient())  # type: ignore[arg-type]
    mcp = build_mcp_server(registry)
    from fastmcp import Client

    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert {"get_issue", "search_issues", "read_file"} <= set(names)
        result = await client.call_tool("get_issue", {"repo": "freeisle/ragent", "number": 67})
        # FastMCP 2.x 返回 CallToolResult; content 为结构化块
        assert result is not None
        text = str(result)
        assert "fake-freeisle/ragent-67" in text


@pytest.mark.asyncio
async def test_mcp_param_validation_error_is_structured() -> None:
    registry = github_tools.build_github_tools(client=FakeGitHubClient())  # type: ignore[arg-type]
    mcp = build_mcp_server(registry)
    from fastmcp import Client

    async with Client(mcp) as client:
        # 缺 number: FastMCP 按签名校验, 返回结构化错误而非异常
        result = await client.call_tool("get_issue", {"repo": "freeisle/ragent"}, raise_on_error=False)
        assert result.is_error is True
