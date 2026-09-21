"""MCP Server：把同一 ToolRegistry 暴露给外部 MCP 客户端(Claude Desktop 等)。

工具层双接口: 内部 LLM 直调(registry.call) + 外部 MCP 协议(本文件)。
写工具永不通过 MCP 暴露。
"""
from fastmcp import FastMCP

from .github_tools import ReadFileArgs, RepoIssueArgs, SearchIssuesArgs, build_github_tools
from .registry import ToolRegistry


def build_mcp_server(registry: ToolRegistry | None = None) -> FastMCP:
    registry = registry or build_github_tools()
    mcp = FastMCP("maintainer-copilot")

    async def get_issue(repo: str, number: int) -> dict:
        """读取单个 issue 的完整内容。"""
        r = await registry.call("get_issue", {"repo": repo, "number": number})
        return {"error": r.error, "hint": r.hint} if not r.ok else {"data": r.data}

    async def search_issues(repo: str, query: str, per_page: int = 30) -> dict:
        """在指定仓库搜索 issue。"""
        r = await registry.call("search_issues", {"repo": repo, "query": query, "per_page": per_page})
        return {"error": r.error, "hint": r.hint} if not r.ok else {"data": r.data}

    async def read_file(repo: str, path: str, ref: str | None = None) -> dict:
        """读取仓库内指定文件内容。"""
        r = await registry.call("read_file", {"repo": repo, "path": path, "ref": ref})
        return {"error": r.error, "hint": r.hint} if not r.ok else {"data": r.data}

    mcp.add_tool(get_issue)
    mcp.add_tool(search_issues)
    mcp.add_tool(read_file)
    return mcp
