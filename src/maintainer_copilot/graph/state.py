"""Agent 全局状态：LangGraph 单图共享状态。

字段约定:
- messages: 用户/助手对话（dict 形式, role+content）
- task_type: route 节点路由结果 (triage | solve | review)
- draft: Worker 产出的对外草稿（评论文本）
- draft_meta: 结构化动作（label 建议等）
- citations: 草稿引用的知识库 chunk 出处
- approval: HumanGate 人工决策 (pending | approved | edited | rejected)
- final_draft: 维护者编辑后的版本
- reflection: Reflector 自审输出 {passed, issues}
- rewrite_count: 重写次数（>=2 走降级）
- final_action: Executor 执行结果（仅批准后填充）
"""
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class Citation(TypedDict, total=False):
    source: str  # code | doc | issue
    path: str  # 文件路径或 issue 编号
    snippet: str  # 引用片段


def message_text(msg: Any) -> str:
    """取消息文本: add_messages 会把 dict 还原成 HumanMessage 对象, 需兼容两种形态。"""
    if isinstance(msg, dict):
        return str(msg.get("content", ""))
    content = getattr(msg, "content", "")
    if isinstance(content, list):  # 多模态内容块
        return " ".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


class AgentState(TypedDict, total=False):
    repo: str  # "owner/name"
    task_type: str
    messages: Annotated[list[dict], add_messages]
    issue: dict  # {"title", "body", "number"} - triage 任务的输入
    draft: str
    draft_meta: dict
    citations: list[Citation]
    approval: str
    final_draft: str
    reflection: dict
    rewrite_count: int
    reflect_advice: str  # Reflector 驳回时附带的改写建议, 供 Worker 重写
    final_action: dict
