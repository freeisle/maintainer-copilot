"""Reflector 单测：三查判定 / 改写建议流转 / 空草稿。"""
import pytest

from maintainer_copilot.graph import reflector


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    async def chat(self, messages, **kwargs):
        return type("R", (), {"content": self.content})()


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch):
    def _set(content: str) -> None:
        monkeypatch.setattr(reflector, "_get_llm", lambda: FakeLLM(content))

    return _set


def test_extract_reflection_plain() -> None:
    parsed = reflector.extract_reflection(
        '{"passed": true, "issues": [], "rewrite_suggestion": ""}'
    )
    assert parsed["passed"] is True


@pytest.mark.asyncio
async def test_passed_draft_no_rewrite(fake_llm) -> None:
    fake_llm('{"passed": true, "issues": [], "rewrite_suggestion": ""}')
    out = await reflector.reflector_node(
        {"draft": "带引用的回答 [1]", "citations": [{"path": "x"}], "repo": "r", "task_type": "triage"}
    )
    assert out["reflection"]["passed"] is True
    assert out["rewrite_count"] == 0
    assert "reflect_advice" not in out


@pytest.mark.asyncio
async def test_failed_draft_with_advice(fake_llm) -> None:
    fake_llm('{"passed": false, "issues": ["无引用"], "rewrite_suggestion": "补充引用编号"}')
    out = await reflector.reflector_node(
        {"draft": "无引用的断言", "citations": [], "repo": "r", "task_type": "triage", "rewrite_count": 0}
    )
    assert out["reflection"]["passed"] is False
    assert out["rewrite_count"] == 1
    assert out["reflect_advice"] == "补充引用编号"


@pytest.mark.asyncio
async def test_empty_draft_fails_without_llm(fake_llm) -> None:
    # 空草稿不调 LLM, 直接判不过
    out = await reflector.reflector_node({"draft": "", "rewrite_count": 0})
    assert out["reflection"]["passed"] is False
    assert out["rewrite_count"] == 1
