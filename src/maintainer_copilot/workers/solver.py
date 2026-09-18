"""SolverWorker 子图：代码库问答（Sprint 1 D5 实现）。

内部链路: QueryRewrite -> Retrieve(混合检索+rerank) -> Answer(带引用)
脚手架阶段返回占位回答, 保证整图可运行。
"""
from typing import Any

from ..graph.state import AgentState


async def solver_node(state: AgentState) -> dict[str, Any]:
    # TODO(Sprint 1 D5): 接入 rag 管线, 生成带引用回答
    question = ""
    if state.get("messages"):
        question = str(state["messages"][-1].get("content", ""))
    return {
        "draft": f"[STUB] SolverWorker 占位回答: 已收到问题「{question[:50]}」（Sprint 1 实现）",
        "citations": [],
    }
