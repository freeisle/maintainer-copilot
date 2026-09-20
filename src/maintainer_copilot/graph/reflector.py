"""Reflector 节点：草稿自审三查（有出处 / 答所问 / 符合规范）。

- 失败且重写次数未超限: 附改写建议回原 Worker 重写(≤2 次)
- 重写超限或空草稿: 判不过 -> 走降级路径(仅提示, 不发布)
"""
import logging
import re
from typing import Any

from ..models.llm import ModelProvider
from .state import AgentState, message_text

logger = logging.getLogger(__name__)

MAX_REWRITES = 2

REFLECTION_PROMPT = """你是质量审查员, 对下面的 Agent 草稿做三查。

任务上下文含两部分: 原始任务输入(task_input)与可用引用资料(编号列表 citations)。

审查要点:
1. 有出处: 草稿中引用资料的具体结论, 编号 [n] 必须与 citations 列表编号一一对应, 且结论与对应资料内容一致; 引用 task_input 本身的信息不算无出处
2. 答所问: 是否回应了 task_input 的诉求; 对 triage 任务, 分类判断是任务本身, 审查其论证是否基于资料、理由是否充分, 而非要求"被资料证明"
3. 符合规范: 语气符合维护者助理定位; 不得编造 task_input 与 citations 中都不存在的事实; 不得承诺无法兑现的动作

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
    # 原始任务输入: 自审需要对照"用户到底说了什么", 避免误判引用
    task_input: dict[str, Any] = {}
    issue = state.get("issue") or {}
    if issue.get("title") or issue.get("body"):
        task_input = {
            "issue_title": issue.get("title", ""),
            "issue_body": (issue.get("body", "") or "")[:500],
        }
    elif state.get("messages"):
        task_input = {"question": message_text(state["messages"][-1])[:500]}
    # 引用资料: 编号化, 供草稿 [n] 一一核验
    citation_lines = "\n".join(
        f"[{i}] ({c.get('source', '')}:{c.get('path', '')}) {c.get('snippet', '')[:150]}"
        for i, c in enumerate(state.get("citations", []), 1)
    )
    context = {
        "repo": state.get("repo", ""),
        "task_type": state.get("task_type", ""),
        "task_input": task_input,
        "citations": citation_lines or "(无引用资料)",
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
    logger.info("自审结果: passed=%s issues=%s", passed, parsed.get("issues", []))
    updates: dict[str, Any] = {
        "reflection": {"passed": passed, "issues": parsed.get("issues", [])},
    }
    if not passed and rewrite_count < MAX_REWRITES:
        updates["rewrite_count"] = rewrite_count + 1
        updates["reflect_advice"] = parsed.get("rewrite_suggestion", "")
    else:
        updates["rewrite_count"] = rewrite_count + (0 if passed else 1)
    return updates
