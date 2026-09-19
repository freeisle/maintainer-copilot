"""查询改写：术语补全 / 中英映射 / 报错串抽取。

报错串是 issue 场景的检索指纹 —— 精确字符串检索优先于语义检索。
TODO(D5): LLM 改写(术语补全、多意图拆分)。
"""
import re

_ERR_PATTERNS = [
    # [\w.$]* 向前回捕包名/类名, 拿到完整的 java.lang.NullPointerException 而非从 Exception 截断
    re.compile(r"(?i)[\w.$]*(?:exception|error|failed|fatal|traceback)[^\n]{0,200}"),
    re.compile(r"(?i)(?:caused by|cannot|unable to)[^\n]{0,120}"),
]


def extract_error_strings(query: str) -> list[str]:
    """从提问中抽取报错特征串, 供精确检索通道使用。"""
    found: list[str] = []
    for pattern in _ERR_PATTERNS:
        for m in pattern.finditer(query):
            found.append(m.group(0).strip()[:200])
    return found
