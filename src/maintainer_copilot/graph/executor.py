"""Executor 节点：唯一可执行写操作的位置。

设计要点: 写工具(评论/标签)不进任何 LLM 的工具列表, 只在此处由确定性代码
在 HumanGate 批准后执行。LLM 只能产出草稿, 不能触碰写权限。
- 默认演练模式(dry_run): 记录将要执行的动作但不发起真实 GitHub 写请求
"""
import logging
from typing import Any

from ..config import get_settings
from ..metrics.adoption import get_store
from ..tools.github_client import GitHubClient
from .state import AgentState

logger = logging.getLogger(__name__)


def _record_decision(state: AgentState) -> None:
    """采集 HITL 决策事件(采纳率数据源); 失败只告警, 不影响主流程。

    仅记录人工决策: 闸门恢复后 state 里必有 approval 键;
    自审降级路径没有 approval 键, 不是人工决策, 不记录。
    """
    if "approval" not in state:
        return
    try:
        meta = state.get("draft_meta") or {}
        note = state.get("final_draft") or state.get("draft", "")
        get_store().record(
            repo=state.get("repo", ""),
            issue_number=meta.get("issue_number"),
            worker=state.get("task_type", ""),
            decision=state.get("approval", "rejected"),
            note=note,
        )
    except Exception as exc:  # noqa: BLE001 - 指标采集绝不阻断发布链路
        logger.warning("决策事件记录失败(不影响执行): %s", exc)


async def executor_node(state: AgentState) -> dict[str, Any]:
    _record_decision(state)
    approval = state.get("approval", "rejected")
    if approval not in ("approved", "edited"):
        logger.info("未获批准, 跳过执行 (approval=%s)", approval)
        return {"final_action": {"executed": False, "reason": f"approval={approval}"}}
    draft = state.get("draft", "")
    if approval == "edited":
        draft = state.get("final_draft", draft)  # 维护者编辑后的版本
    meta = state.get("draft_meta") or {}
    repo = state.get("repo", "")
    number = meta.get("issue_number")
    labels = meta.get("labels") or []
    actions = {
        "comment": {"repo": repo, "issue": number, "body": draft},
        "labels": {"repo": repo, "issue": number, "labels": labels},
    }
    settings = get_settings()
    if settings.dry_run:
        logger.info("[dry-run] 跳过真实写操作: %s", {k: v for k, v in actions.items()})
        return {"final_action": {"executed": True, "dry_run": True, "actions": actions}}
    client = GitHubClient(token=settings.github_token)
    results: dict[str, Any] = {}
    if number:
        results["comment"] = await client.add_comment(repo, number, draft)
    if number and labels:
        try:
            results["labels"] = await client.add_labels(repo, number, labels)
        except Exception as exc:  # noqa: BLE001 - 标签不存在时 GitHub 整体拒绝, 不影响评论发布
            logger.warning("标签写入失败(评论已发布, 忽略标签错误): %s", exc)
            results["labels_error"] = str(exc)
    return {"final_action": {"executed": True, "dry_run": False, "results": results}}
