"""查询改写：术语补全 / 中英映射 / 多意图拆分(LLM) + 报错串抽取(规则)。

设计: 原问题永远保留(防止 LLM 改写跑偏), 改写结果最多追加 2 条。
"""
import logging
import re

logger = logging.getLogger(__name__)

_ERR_PATTERNS = [
    # [\w.$]* 向前回捕包名/类名, 拿到完整的 java.lang.NullPointerException 而非从 Exception 截断
    re.compile(r"(?i)[\w.$]*(?:exception|error|failed|fatal|traceback)[^\n]{0,200}"),
    re.compile(r"(?i)(?:caused by|cannot|unable to)[^\n]{0,120}"),
]

REWRITE_PROMPT = """你是代码库检索助手。把用户问题改写为最多 2 条更适合检索的查询:
- 补全术语/缩写(如 "候补" -> "候补购票 waitlist")
- 生成中英变体(代码库检索对英文关键词命中更好)
- 拆分多意图
只输出查询, 每行一条, 不要解释。

用户问题: {question}
"""


def extract_error_strings(query: str) -> list[str]:
    """从提问中抽取报错特征串, 供精确检索通道使用。"""
    found: list[str] = []
    for pattern in _ERR_PATTERNS:
        for m in pattern.finditer(query):
            found.append(m.group(0).strip()[:200])
    return found


class QueryRewriter:
    def __init__(self, llm) -> None:
        self.llm = llm

    async def rewrite(self, question: str) -> list[str]:
        """LLM 改写; 失败时返回空列表(调用方用原问题兜底)。"""
        try:
            result = await self.llm.chat(
                [{"role": "user", "content": REWRITE_PROMPT.format(question=question)}],
                temperature=0.0,
                max_tokens=200,
            )
            lines = [line.strip(" -•*") for line in result.content.splitlines() if line.strip()]
            return [line for line in lines if line != question][:2]
        except Exception as exc:  # noqa: BLE001 - 改写失败不阻断主链路
            logger.warning("查询改写失败, 用原问题兜底: %s", exc)
            return []
