"""ModelProvider 单测：主备切换与全链路失败。"""
import pytest

from maintainer_copilot.config import Settings
from maintainer_copilot.models.llm import ModelProvider


class FakeCompletions:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise ConnectionError("boom")
        return _FakeResp()


class _FakeChoice:
    class Message:
        content = "你好"
        tool_calls = []

    message = Message()


class _FakeResp:
    model = "fake-model"
    choices = [_FakeChoice()]


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.chat = type("Chat", (), {"completions": FakeCompletions(fail=fail)})()


@pytest.fixture
def provider() -> ModelProvider:
    settings = Settings(deepseek_api_key="sk-test")
    llm = ModelProvider(settings)
    llm.primary = FakeClient()  # type: ignore[assignment]
    llm.fallback = FakeClient()  # type: ignore[assignment]
    return llm


@pytest.mark.asyncio
async def test_primary_success(provider: ModelProvider) -> None:
    r = await provider.chat([{"role": "user", "content": "hi"}])
    assert r.content == "你好"
    assert r.used_fallback is False


@pytest.mark.asyncio
async def test_fallback_on_primary_failure(provider: ModelProvider) -> None:
    provider.primary = FakeClient(fail=True)  # type: ignore[assignment]
    r = await provider.chat([{"role": "user", "content": "hi"}])
    assert r.used_fallback is True
    assert provider.primary.chat.completions.calls == 1  # type: ignore[attr-defined]
    assert provider.fallback.chat.completions.calls == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_both_channels_fail_raises(provider: ModelProvider) -> None:
    provider.primary = FakeClient(fail=True)  # type: ignore[assignment]
    provider.fallback = FakeClient(fail=True)  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="均不可用"):
        await provider.chat([{"role": "user", "content": "hi"}])
