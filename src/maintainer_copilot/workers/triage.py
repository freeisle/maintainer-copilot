"""TriageWorker 子图：Issue 分诊（Sprint 2 D6 实现）。

职责: 相似 issue 检索 -> 分类(bug/feature/question/invalid) -> label 建议 -> 回复草稿
脚手架阶段返回占位草稿, 保证整图可运行。
"""
from typing import Any

from ..graph.state import AgentState


async def triage_node(state: AgentState) -> dict[str, Any]:
    # TODO(Sprint 2 D6): 相似 issue 检索 + LLM 分类 + 草稿生成
    return {
        "draft": "[STUB] TriageWorker 分诊草稿（Sprint 2 实现）",
        "draft_meta": {"labels": ["needs-triage"]},
        "citations": [],
    }
