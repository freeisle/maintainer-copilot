"""记忆模块单测: 短期裁剪 / 长期偏好冲突不覆盖。"""
from pathlib import Path

import pytest

from maintainer_copilot.config import Settings
from maintainer_copilot.memory import long_term, short_term


def test_trim_within_budget_untouched() -> None:
    msgs = [{"role": "user", "content": "短消息"}]
    kept, summary = short_term.trim_messages(msgs, max_tokens=1000)
    assert kept == msgs and summary is None


def test_trim_keeps_anchor_and_recent() -> None:
    msgs = [{"role": "user", "content": "任务锚点"}] + [
        {"role": "assistant", "content": "长" * 500} for _ in range(8)
    ]
    kept, summary = short_term.trim_messages(msgs, max_tokens=500, keep_recent=3)
    assert kept[0] == msgs[0]
    assert kept[-3:] == msgs[-3:]
    assert summary is not None


def test_estimate_tokens_cjk() -> None:
    assert short_term.estimate_tokens("中文四个") == 4


@pytest.fixture
def tmp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(long_term, "get_settings", lambda: settings)


def test_preference_conflict_flow(tmp_settings: None) -> None:
    repo = "freeisle/12306"
    assert long_term.update_preference(repo, "reply_language", "zh")["applied"] is True
    result = long_term.update_preference(repo, "reply_language", "en")
    assert result["conflict"] is True  # 冲突不覆盖
    mem = long_term.load_memory(repo)
    assert mem["preferences"]["reply_language"] == "zh"
    assert long_term.resolve_conflict(repo, "reply_language", "new")["resolved"] == "new"
    assert long_term.load_memory(repo)["preferences"]["reply_language"] == "en"


def test_preference_history_recorded(tmp_settings: None) -> None:
    repo = "freeisle/ragent"
    long_term.update_preference(repo, "tone", "concise")
    mem = long_term.load_memory(repo)
    assert len(mem["history"]) == 1
    assert mem["history"][0]["key"] == "tone"
