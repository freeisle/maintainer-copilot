"""Executor 单测：批准闸门 / 演练模式 / 编辑后文本。"""
from pathlib import Path

import pytest

from maintainer_copilot.config import Settings
from maintainer_copilot.graph import executor


@pytest.fixture
def dry_run_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: Settings(dry_run=True, data_dir=tmp_path))


@pytest.mark.asyncio
async def test_rejected_skips_execution(dry_run_settings) -> None:
    out = await executor.executor_node({"approval": "rejected", "draft": "x"})
    assert out["final_action"]["executed"] is False


@pytest.mark.asyncio
async def test_approved_dry_run_records_actions(dry_run_settings) -> None:
    state = {
        "approval": "approved",
        "draft": "回复草稿",
        "repo": "freeisle/ragent",
        "draft_meta": {"labels": ["bug"], "issue_number": 67},
    }
    out = await executor.executor_node(state)
    action = out["final_action"]
    assert action["executed"] is True
    assert action["dry_run"] is True
    assert action["actions"]["comment"]["issue"] == 67
    assert action["actions"]["labels"]["labels"] == ["bug"]


@pytest.mark.asyncio
async def test_edited_uses_final_draft(dry_run_settings) -> None:
    state = {
        "approval": "edited",
        "draft": "原始草稿",
        "final_draft": "维护者修改后的文本",
        "repo": "freeisle/ragent",
        "draft_meta": {"issue_number": 67},
    }
    out = await executor.executor_node(state)
    assert out["final_action"]["actions"]["comment"]["body"] == "维护者修改后的文本"
