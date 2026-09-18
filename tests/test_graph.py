"""状态机骨架单测: 图可编译 + 节点齐备。"""
from langgraph.checkpoint.memory import InMemorySaver

from maintainer_copilot.graph.supervisor import build_graph

EXPECTED_NODES = {"route", "triage", "solve", "review", "reflector", "gate", "degrade", "executor"}


def test_build_graph_compiles() -> None:
    graph = build_graph(checkpointer=InMemorySaver())
    nodes = set(graph.get_graph().nodes.keys())
    assert EXPECTED_NODES <= nodes
    # 写操作闸门: 唯一通向 END 的节点必须是 executor
    edges = graph.get_graph().edges
    to_end = {e.source for e in edges if e.target == "__end__"}
    assert to_end == {"executor"}
