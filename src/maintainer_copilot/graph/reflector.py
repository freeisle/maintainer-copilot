"""Reflector 节点：草稿自审三查（有出处 / 答所问 / 符合规范）。

- 失败且重写次数未超限: 附改写建议回原 Worker 重写(≤2 次)
- 重写超限或空草稿: 判不过 -> 走降级路径(仅提示, 不发布)
"""
import logging
import re
from typing import Any

from ..models.llm import ModelProvider
from .state import AgentState

logger = logging.getLogger(__name__)

MAX_REWRITES = 2

REFLECTION_PROMPT = """你是质量审查员, 对下面的 Agent 草稿做三查:

1. 有出处: 草稿中的具体断言(代码细节/配置项/接口名/数据)是否都有引用编号 [n] 支撑? 无引用的具体断言计为问题
2. 答所问: 是否直接回应了任务? 有无答非所问或遗漏关键信息?
3. 符合规范: 语气是否符合维护者助理定位? 有无编造事实或承诺无法兑现的行为?

草稿:
{content}

任务上下文:
{context}

只输出 JSON: {{"passed": bool, "issues": [str], "rewrite_suggestion": str}}
"""


def extract_reflection(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("未找到 JSON")
    import json

    return json.loads(m.group(0))


_llm: ModelProvider | None = None


def _get_llm() -> ModelProvider:
    global _llm
    if _llm is None:
        _llm = ModelProvider()
    return _llm


async def reflector_node(state: AgentState) -> dict[str, Any]:
    draft = state.get("draft", "")
    rewrite_count = state.get("rewrite_count", 0)
    if not draft:
        return {
            "reflection": {"passed": False, "issues": ["空草稿(可能已触发低置信度降级)"]},
            "rewrite_count": rewrite_count + 1,
        }
    context = {
        "repo": state.get("repo", ""),
        "task_type": state.get("task_type", ""),
        "citations": [c.get("path", "") for c in state.get("citations", [])],
    }
    result = await _get_llm().chat(
        [
            {
                "role": "user",
                "content": REFLECTION_PROMPT.format(content=draft[:2000], context=context),
            }
        ],
        temperature=0.0,
    )
    try:
        parsed = extract_reflection(result.content)
    except (ValueError, ImportError) as exc:
        logger.warning("自审输出不可解析, 保守判不过: %s", exc)
        parsed = {"passed": False, "issues": ["自审输出不可解析"], "rewrite_suggestion": ""}
    passed = bool(parsed.get("passed", False))
    updates: dict[str, Any] = {
        "reflection": {"passed": passed, "issues": parsed.get("issues", [])},
    }
    if not passed and rewrite_count < MAX_REWRITES:
        updates["rewrite_count"] = rewrite_count + 1
        updates["reflect_advice"] = parsed.get("rewrite_suggestion", "")
    else:
        updates["rewrite_count"] = rewrite_count + (0 if passed else 1)
    return updates
