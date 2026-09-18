"""Reflector 节点：草稿自审三查（有出处 / 答所问 / 符合规范）。

Sprint 2 用 LLM 实现三查与重写决策；脚手架阶段返回占位判定，保证整图可编译可运行。
"""
from typing import Any

from .state import AgentState

MAX_REWRITES = 2


async def reflector_node(state: AgentState) -> dict[str, Any]:
    # TODO(Sprint 2 D7): LLM 三查 + 失败重写 + 降级逻辑
    draft = state.get("draft", "")
    passed = bool(draft)
    return {
        "reflection": {"passed": passed, "issues": [] if passed else ["空草稿"]},
        "rewrite_count": state.get("rewrite_count", 0) + (0 if passed else 1),
    }
