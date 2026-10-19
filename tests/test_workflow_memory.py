import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from maintainer_copilot.graph import supervisor
from maintainer_copilot.memory.short_term import trim_messages, estimate_tokens, SUMMARY_PREFIX


def test_summary_is_bounded_and_not_nested():
    messages = [{"role":"user", "content":"task anchor"}]
    for i in range(40):
        messages += [{"role":"user", "content":str(i) + "中" * 400}]
        messages, _ = trim_messages(messages, max_tokens=500, keep_recent=2, strict_budget=True)
        assert sum(estimate_tokens(m["content"]) for m in messages) <= 500
        assert messages[0]["content"] == "task anchor"
        assert sum(m["content"].count(SUMMARY_PREFIX) for m in messages) <= 1


@pytest.mark.asyncio
async def test_graph_multiturn_reset_isolation_and_anchor(monkeypatch):
    seen = []
    async def solve(state):
        seen.append(state)
        return {"draft":"answer", "citations":[]}
    async def reflect(state):
        return {"reflection":{"passed":True}}
    monkeypatch.setattr(supervisor, "solver_node", solve)
    monkeypatch.setattr(supervisor, "reflector_node", reflect)
    graph = supervisor.build_graph(InMemorySaver())
    config = {"configurable":{"thread_id":"one"}}
    for text in ("anchor", "follow up"):
        await graph.ainvoke({"repo":"a/b", "messages":[{"role":"user", "content":text}]}, config)
        assert (await graph.aget_state(config)).next == ("gate",)
        await graph.ainvoke(Command(resume={"decision":"approved"}), config)
    assert [m.content for m in seen[-1]["messages"]] == ["anchor", "answer", "follow up"]
    assert seen[-1]["approval"] == "pending" and seen[-1]["rewrite_count"] == 0
    assert not (await graph.aget_state({"configurable":{"thread_id":"two"}})).values
    with pytest.raises(ValueError, match="switch repositories"):
        await graph.ainvoke({"repo":"other/repo", "messages":[{"role":"user","content":"q"}]}, config)
