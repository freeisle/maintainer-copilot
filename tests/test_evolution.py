from types import SimpleNamespace
import pytest
from maintainer_copilot.skills.evolution import EvolutionStore
from maintainer_copilot.skills.loader import skill_context
from maintainer_copilot.graph.executor import executor_node


class Generator:
    async def chat(self, messages, **kwargs):
        assert "维护者修订" in messages[0]["content"]
        return SimpleNamespace(content="# FAQ\n请附上版本和复现步骤。")


@pytest.mark.asyncio
async def test_feedback_review_activation_rollback():
    store = EvolutionStore()
    state = dict(repo="a/b", turn_id="one", approval="edited", draft="错误"*500,
                 final_draft="维护者修订", task_type="triage")
    await executor_node(state)
    await executor_node(state)
    with store.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM skill_feedback").fetchone()[0] == 1
        assert "错误"*500 in c.execute("SELECT payload FROM skill_feedback").fetchone()[0]
    cid = await store.generate("a/b", Generator())
    assert await store.generate("a/b", Generator()) == cid
    assert "# FAQ" not in skill_context("a/b")
    store.decide(cid,"approved","maintainer","已核验引用规则")
    assert "# FAQ" in skill_context("a/b")
    assert "# FAQ" not in skill_context("other/repo")
    with pytest.raises(ValueError):
        store.decide(cid,"approved","maintainer")
    store.rollback("a/b",0,"maintainer")
    assert "# FAQ" not in skill_context("a/b")
    assert len(store.history("a/b")) == 2


@pytest.mark.asyncio
async def test_empty_feedback_and_reject_never_activate():
    store = EvolutionStore()
    with pytest.raises(ValueError, match="No reviewed"):
        await store.generate("a/b",Generator())
    store.feedback(dict(repo="a/b",approval="rejected",draft="bad"))
    cid = await store.generate("a/b",Generator())
    store.decide(cid,"rejected","maintainer")
    assert store.active("a/b")["version"] == 0
