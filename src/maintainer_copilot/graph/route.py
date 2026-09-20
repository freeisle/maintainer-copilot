"""Route 节点：任务级意图路由（issue 事件 / 问答 / PR 事件）。

两级意图识别的第一级。第二级（bug/feature/question/invalid）在 TriageWorker 内完成。
路由策略: 规则兜底 + LLM 精判（脚手架先落地规则, LLM 精判在 Sprint 2 替换, 并记录路由决策到 trace）。
"""
import re
from typing import Any

from .state import AgentState, message_text

_ISSUE_HINTS = re.compile(r"(issue|问题单|缺陷|#\d+)", re.IGNORECASE)
_PR_HINTS = re.compile(r"(pull request|merge request|\bpr\b|review)", re.IGNORECASE)


def route_by_rules(text: str) -> str:
    """确定性规则路由：零成本、可单测、可解释。"""
    if _PR_HINTS.search(text):
        return "review"
    if _ISSUE_HINTS.search(text):
        return "triage"
    return "solve"


async def route_node(state: AgentState) -> dict[str, Any]:
    # TODO(Sprint 2): 规则置信度低时升级 LLM 精判
    text = message_text(state["messages"][-1]) if state.get("messages") else ""
    return {"task_type": state.get("task_type") or route_by_rules(text)}
