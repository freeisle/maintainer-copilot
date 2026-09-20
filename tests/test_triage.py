"""TriageWorker 单测：LLM 输出 JSON 解析。"""
import pytest

from maintainer_copilot.workers.triage import extract_json


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
