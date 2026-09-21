"""工具选择器：按查询意图从读工具列表中选择一个工具(或 none)。

用途: 工具选择评测集(P1-6)的受测组件; 工具描述按"何时用/何时不用"迭代时,
选择准确率量化描述质量。写操作不进 LLM 工具列表(安全设计), 所以涉及写操作的
查询应选择 none。
"""
import logging
import re

from ..models.llm import ModelProvider
from .github_tools import build_github_tools
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

NONE_TOOL = "none"

SELECT_PROMPT = """你是开源维护者助理的工具路由器。根据用户查询, 从下列工具中选择最合适的一个; 都不合适、或查询涉及写操作(评论/打标签/关闭)时选 none。

工具:
{tools}

只输出工具名, 不要任何解释。

用户查询: {query}
"""


def parse_selection(text: str, tool_names: list[str]) -> str:
    """从 LLM 输出提取工具名; 未知输出一律 none(保守: 宁可不选, 不误选)。"""
    for token in re.findall(r"[a-z_]+", text.lower()):
        if token in tool_names:
            return token
    return NONE_TOOL


class ToolSelector:
    def __init__(self, llm=None, registry: ToolRegistry | None = None) -> None:
        self.llm = llm or ModelProvider()
        self.registry = registry or build_github_tools()

    def tool_descriptions(self) -> str:
        """读工具列表(名称+描述), 供 Prompt 与评测检查描述完整性。"""
        return "\n".join(
            f"- {name}: {self.registry.get(name).description}"
            for name in self.registry.tool_names()
        )

    async def select(self, query: str) -> str:
        prompt = SELECT_PROMPT.format(tools=self.tool_descriptions(), query=query)
        result = await self.llm.chat(
            [{"role": "user", "content": prompt}], temperature=0.0
        )
        return parse_selection(result.content, self.registry.tool_names())
