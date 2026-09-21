"""工具选择器单测：解析 / Prompt 完整性 / 未知输出兜底。"""
from types import SimpleNamespace

import pytest

from maintainer_copilot.tools.github_tools import build_github_tools
from maintainer_copilot.tools.selector import (
    NONE_TOOL,
    ToolSelector,
    parse_selection,
)


def test_parse_selection_picks_known_tool() -> None:
    assert parse_selection("应该用 get_issue", ["get_issue", "search_issues"]) == "get_issue"
    assert parse_selection("```\nsearch_issues\n```", ["get_issue", "search_issues"]) == "search_issues"


def test_parse_selection_unknown_falls_back_none() -> None:
    assert parse_selection("我不确定", ["get_issue"]) == NONE_TOOL
    assert parse_selection("", ["get_issue"]) == NONE_TOOL


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(content=self.content)


@pytest.mark.asyncio
async def test_select_returns_tool_from_llm() -> None:
    llm = _FakeLLM("read_file")
    registry = build_github_tools()  # 只注册, 不发请求
    selected = await ToolSelector(llm=llm, registry=registry).select("pom.xml 怎么配的")
    assert selected == "read_file"


@pytest.mark.asyncio
async def test_select_prompt_contains_all_tool_descriptions() -> None:
    llm = _FakeLLM("none")
    registry = build_github_tools()
    await ToolSelector(llm=llm, registry=registry).select("给这个 issue 打标签")
    prompt = llm.calls[0][0]["content"]
    for name in ("get_issue", "search_issues", "read_file"):
        assert f"- {name}: " in prompt
    assert "写操作" in prompt  # 安全规则: 写请求必须选 none
