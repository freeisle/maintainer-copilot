"""HITL 决策事件采集单测：记录路径 / 采纳率汇总 / degrade 不计入。"""
import pytest

from maintainer_copilot.graph import executor
from maintainer_copilot.metrics.adoption import DecisionStore, get_store


def test_record_and_summary_adoption_rate(tmp_path) -> None:
    store = DecisionStore(tmp_path / "adoption.sqlite")
    store.record(repo="r", issue_number=1, worker="triage", decision="approved", note="a")
    store.record(repo="r", issue_number=2, worker="triage", decision="edited", note="b")
    store.record(repo="r", issue_number=3, worker="triage", decision="rejected", note="c")
    s = store.summary()
    assert s["total_decisions"] == 3
    assert s["adopted"] == 2
    assert s["rejected"] == 1
    assert s["adoption_rate"] == pytest.approx(0.667, abs=0.001)
    assert s["by_worker"]["triage"] == {"approved": 1, "edited": 1, "rejected": 1}


def test_summary_empty_has_no_rate(tmp_path) -> None:
    assert DecisionStore(tmp_path / "adoption.sqlite").summary() == {
        "total_decisions": 0,
        "adopted": 0,
        "rejected": 0,
        "adoption_rate": None,
        "by_worker": {},
    }


@pytest.mark.asyncio
async def test_executor_approved_records_decision() -> None:
    state = {
        "approval": "approved",
        "draft": "回复草稿",
        "repo": "freeisle/ragent",
        "task_type": "triage",
        "draft_meta": {"labels": ["bug"], "issue_number": 67},
    }
    await executor.executor_node(state)
    s = get_store().summary()
    assert s["total_decisions"] == 1
    assert s["adoption_rate"] == 1.0


@pytest.mark.asyncio
async def test_executor_rejected_records_decision() -> None:
    state = {"approval": "rejected", "draft": "x", "repo": "r", "task_type": "triage"}
    await executor.executor_node(state)
    s = get_store().summary()
    assert s["total_decisions"] == 1
    assert s["rejected"] == 1


@pytest.mark.asyncio
async def test_edited_records_final_draft_note() -> None:
    state = {
        "approval": "edited",
        "draft": "原始",
        "final_draft": "维护者编辑版",
        "repo": "r",
        "task_type": "triage",
        "draft_meta": {"issue_number": 1},
    }
    await executor.executor_node(state)
    store = get_store()
    with store._conn() as conn:
        row = conn.execute("SELECT note FROM hitl_decisions").fetchone()
    assert row["note"] == "维护者编辑版"


@pytest.mark.asyncio
async def test_degrade_without_approval_not_recorded() -> None:
    """自审降级不是人工决策: state 无 approval 键, 不产生事件。"""
    state = {"draft": "", "repo": "r", "task_type": "triage", "reflection": {"passed": False}}
    await executor.executor_node(state)
    assert get_store().summary()["total_decisions"] == 0
