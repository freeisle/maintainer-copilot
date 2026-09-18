"""Executor 节点：唯一可执行写操作的位置。

设计要点: 写工具(评论/标签)不进任何 LLM 的工具列表, 只在此处由确定性代码
在 HumanGate 批准后执行。LLM 只能产出草稿, 不能触碰写权限。
"""
import logging
from typing import Any

from .state import AgentState

logger = logging.getLogger(__name__)


async def executor_node(state: AgentState) -> dict[str, Any]:
    approval = state.get("approval", "rejected")
    if approval not in ("approved", "edited"):
        logger.info("未获批准, 跳过执行 (approval=%s)", approval)
        return {"final_action": {"executed": False, "reason": f"approval={approval}"}}
    draft = state.get("draft", "")
    if approval == "edited":
        draft = state.get("final_draft", draft)  # 维护者编辑后的版本
    # TODO(Sprint 2 D8): 按 draft_meta 调用 GitHub 写接口 (add_comment / add_labels)
    logger.info("批准执行草稿: %s", draft[:80])
    return {"final_action": {"executed": True, "content": draft}}
