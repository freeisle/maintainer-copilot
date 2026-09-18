"""ReviewWorker 子图：PR 初审（Sprint 2 冲刺项, 可降级）。

职责: 拉取 diff + CI 状态 -> 按 repo 规范生成初审意见
"""
from typing import Any

from ..graph.state import AgentState


async def reviewer_node(state: AgentState) -> dict[str, Any]:
    # TODO(Sprint 2 stretch): diff 分析 + 规范对齐 + 初审意见
    return {
        "draft": "[STUB] ReviewWorker 初审意见（Sprint 2 stretch）",
        "draft_meta": {},
        "citations": [],
    }
