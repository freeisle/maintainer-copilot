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


def test_get_preference_default_while_pending(tmp_settings: None) -> None:
    """裁决前使用默认值: 冲突挂起期间 get_preference 返回 default。"""
    repo = "freeisle/12306"
    long_term.update_preference(repo, "reply_language", "zh")
    long_term.update_preference(repo, "reply_language", "en")  # 冲突挂起
    assert long_term.get_preference(repo, "reply_language") is None
    assert long_term.get_preference(repo, "reply_language", "zh") == "zh"  # 显式 default
    long_term.resolve_conflict(repo, "reply_language", "new")
    assert long_term.get_preference(repo, "reply_language") == "en"


def test_preference_version_increments(tmp_settings: None) -> None:
    """每次生效写入(含重复确认)与裁决后都 version+1; 冲突挂起不递增。"""
    repo = "r"
    assert long_term.update_preference(repo, "k", "a")["version"] == 1
    assert long_term.update_preference(repo, "k", "a")["version"] == 2  # 重复写入也递增
    long_term.update_preference(repo, "k", "b")  # 冲突挂起, 版本不动
    assert long_term.resolve_conflict(repo, "k", "new")["version"] == 3


def test_history_append_only_actions(tmp_settings: None) -> None:
    """事件历史只增不删: set -> conflict_pending -> resolve_new 全链路留痕。"""
    repo = "r"
    long_term.update_preference(repo, "k", "a")
    long_term.update_preference(repo, "k", "b")  # 冲突
    long_term.resolve_conflict(repo, "k", "new")
    actions = [e["action"] for e in long_term.preference_history(repo, "k")]
    assert actions == ["set", "conflict_pending", "resolve_new"]


def test_trim_summary_prefixed_system_message() -> None:
    """摘要以 system 消息插回锚点之后, 带前缀防误引。"""
    msgs = [{"role": "user", "content": "任务锚点"}] + [
        {"role": "assistant", "content": "长" * 500} for _ in range(8)
    ]
    kept, summary = short_term.trim_messages(msgs, max_tokens=500, keep_recent=3)
    assert kept[0] == msgs[0]  # 锚点永不丢
    assert kept[1]["role"] == "system"
    assert short_term.SUMMARY_PREFIX in kept[1]["content"]
    assert kept[-3:] == msgs[-3:]
    assert summary is not None
