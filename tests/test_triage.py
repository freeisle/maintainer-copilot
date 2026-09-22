"""TriageWorker 单测：LLM 输出 JSON 解析 / 判别规则回归。"""
import pytest

from maintainer_copilot.workers.triage import TRIAGE_PROMPT, extract_json


def test_prompt_has_feature_disambiguation_rules() -> None:
    """回归防线: 判别规则与反例不得被悄悄删除(P0-2 feature 误判修复)。"""
    assert "先找\"可复现的错误行为\"" in TRIAGE_PROMPT
    assert "标题以 [Bug] 开头只是 issue 模板前缀" in TRIAGE_PROMPT
    assert "未发生任何错误 → feature(请求行为改变)" in TRIAGE_PROMPT


def test_extract_json_plain() -> None:
    parsed = extract_json('{"category": "bug", "labels": ["bug"], "reply": "x", "confidence": 0.9}')
    assert parsed["category"] == "bug"
    assert parsed["confidence"] == 0.9


def test_extract_json_fenced_with_noise() -> None:
    text = '分诊结果如下:\n```json\n{"category": "question", "labels": [], "reply": "请提供更多信息", "confidence": 0.7}\n```\n以上。'
    parsed = extract_json(text)
    assert parsed["category"] == "question"


def test_extract_json_invalid_raises() -> None:
    with pytest.raises(ValueError):
        extract_json("没有 JSON 的输出")
