"""Supervisor：图组装 + HumanGate 中断。

结构: Route -> (Triage|Solve|Review) -> Reflector -> [HumanGate] -> Executor
- HumanGate 使用 LangGraph interrupt 原生实现 HITL（挂起等待维护者决策）
- Reflector 失败且重写次数用尽 -> 降级为仅提示（不打标签不回复），不进入闸门
- checkpoint: 默认 SQLite（文件式, 可演示恢复执行）, 失败回退内存
"""
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..config import get_settings
from ..workers.reviewer import reviewer_node
from ..workers.solver import solver_node
from ..workers.triage import triage_node
from .executor import executor_node
from .reflector import MAX_REWRITES, reflector_node
from .route import route_node
from .state import AgentState


def route_after_start(state: AgentState) -> str:
    return state.get("task_type") or "solve"


def route_after_reflector(state: AgentState) -> str:
    if not state.get("reflection", {}).get("passed"):
        if state.get("rewrite_count", 0) < MAX_REWRITES:
            return state.get("task_type", "solve")  # 回到原 Worker 重写
        return "degrade"  # 重写超限 -> 仅提示
    return "gate"


def human_gate_node(state: AgentState) -> dict:
    """HITL 闸门：挂起等待维护者 批准/编辑/驳回。"""
    decision = interrupt(
        {
            "type": "human_gate",
            "repo": state.get("repo", ""),
            "task_type": state.get("task_type", ""),
            "draft": state.get("draft", ""),
            "citations": state.get("citations", []),
            "reflection": state.get("reflection", {}),
        }
    )
    approval = decision.get("decision", "rejected")
    updates: dict = {"approval": approval}
    if approval == "edited":
        updates["final_draft"] = decision.get("edited_draft", state.get("draft", ""))
    return updates


def _degrade_node(state: AgentState) -> dict:
    """降级路径：自审不通过且重写超限, 只提示不发布。"""
    return {
        "final_action": {
            "executed": False,
            "reason": "reflector_degraded",
            "note": state.get("reflection", {}).get("issues", []),
        }
    }


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """组装并编译状态机。checkpointer=None 时默认 SQLite 文件, 失败回退内存。"""
    if checkpointer is None:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            db_dir = get_settings().checkpoint_dir
            db_dir.mkdir(parents=True, exist_ok=True)
            checkpointer = SqliteSaver.from_conn_string(str(db_dir / "checkpoints.sqlite"))
        except Exception:  # noqa: BLE001 - 任何持久化问题都不应阻断启动
            checkpointer = InMemorySaver()

    graph = StateGraph(AgentState)
    graph.add_node("route", route_node)
    graph.add_node("triage", triage_node)
    graph.add_node("solve", solver_node)
    graph.add_node("review", reviewer_node)
    graph.add_node("reflector", reflector_node)
    graph.add_node("gate", human_gate_node)
    graph.add_node("degrade", _degrade_node)
    graph.add_node("executor", executor_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", route_after_start, ["triage", "solve", "review"])
    for worker in ("triage", "solve", "review"):
        graph.add_edge(worker, "reflector")
    graph.add_conditional_edges(
        "reflector",
        route_after_reflector,
        {"triage": "triage", "solve": "solve", "review": "review", "gate": "gate", "degrade": "degrade"},
    )
    graph.add_edge("gate", "executor")
    graph.add_edge("degrade", "executor")
    graph.add_edge("executor", END)
    return graph.compile(checkpointer=checkpointer)
