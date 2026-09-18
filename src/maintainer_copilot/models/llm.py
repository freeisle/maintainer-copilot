"""ModelProvider：主备双通道 LLM 抽象。

- 主通道: DeepSeek API（OpenAI 兼容）
- 兜底:   本地 Ollama（同为 OpenAI 兼容协议，仅 base_url 不同）
- 失败策略: 连接/协议错误自动切换兜底并告警；演示不因断网翻车
"""
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    model: str = ""
    used_fallback: bool = False


class ModelProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.deepseek_api_key:
            logger.warning(
                "未检测到 DeepSeek API Key（DEEPSEEK_API_KEY / MC_DEEPSEEK_API_KEY / .env），"
                "主通道将不可用，自动走 Ollama 兜底"
            )
        self.primary = AsyncOpenAI(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
        )
        self.fallback = AsyncOpenAI(
            api_key="ollama",
            base_url=self.settings.ollama_base_url,
        )

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> ChatResult:
        for attempt in (1, 2):
            client, model = self.primary, self.settings.primary_model
            used_fallback = False
            if attempt == 2:
                client, model = self.fallback, self.settings.fallback_model
                used_fallback = True
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                choice = resp.choices[0].message
                return ChatResult(
                    content=choice.content or "",
                    tool_calls=[tc.model_dump() for tc in (choice.tool_calls or [])],
                    model=resp.model,
                    used_fallback=used_fallback,
                )
            except Exception as exc:  # noqa: BLE001 - 任意网络/协议错误统一走兜底
                logger.warning("LLM 调用失败(attempt=%d, model=%s): %s", attempt, model, exc)
                if attempt == 2:
                    raise RuntimeError("主备两个 LLM 通道均不可用") from exc
        raise AssertionError("unreachable")
