"""Tool Registry：工具注册 / 参数校验 / 超时重试 / 结构化错误。

统一约定:
- 每个工具: 函数 + Pydantic args_schema + 描述(说明何时用/何时不用)
- 失败返回结构化 ToolResult(ok=False, error, hint), 由 LLM 决策降级
- 写工具标记 is_write=True: 不进 LLM 工具列表, 仅 Executor 可用
"""
import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    description: str
    fn: Callable[..., Any]
    args_schema: type[BaseModel]
    timeout: float = 30.0
    retries: int = 1
    is_write: bool = False


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    hint: str = ""


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._specs[name]

    def tool_names(self) -> list[str]:
        return list(self._specs)

    def llm_tool_schemas(self) -> list[dict]:
        """供 LLM tool calling 使用: 只暴露读工具。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.args_schema.model_json_schema(),
                },
            }
            for s in self._specs.values()
            if not s.is_write
        ]

    async def call(self, name: str, args: dict) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(
                ok=False, error=f"未知工具: {name}", hint=f"可用工具: {list(self._specs)}"
            )
        try:
            parsed = spec.args_schema.model_validate(args)
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                error=f"参数校验失败: {exc.errors()}",
                hint=f"用法示例: {spec.args_schema.model_json_schema()}",
            )
        for attempt in range(spec.retries + 1):
            try:
                result = spec.fn(**parsed.model_dump())
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=spec.timeout)
                return ToolResult(ok=True, data=result)
            except asyncio.TimeoutError:
                if attempt < spec.retries:
                    continue
                return ToolResult(
                    ok=False,
                    error=f"工具 {name} 超时({spec.timeout}s)",
                    hint="建议改用本地索引或稍后重试",
                )
            except Exception as exc:  # noqa: BLE001 - 工具失败统一转结构化错误
                if attempt < spec.retries:
                    continue
                return ToolResult(
                    ok=False,
                    error=f"工具 {name} 执行失败: {exc}",
                    hint="检查参数或网络后重试",
                )
        raise AssertionError("unreachable")
