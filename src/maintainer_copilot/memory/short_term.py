"""短期记忆：会话消息的滑动窗口裁剪 + 滚动摘要。

- 按 token 预算裁剪(中文按字符粗估, 英文按 4 字符 1 token)
- 超出预算时: 保留首条(任务锚点) + 最近 keep_recent 条, 中间部分生成滚动摘要
"""
import re

_CJK = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + other // 4


def trim_messages(
    messages: list[dict], max_tokens: int = 4000, keep_recent: int = 6
) -> tuple[list[dict], str | None]:
    """返回 (裁剪后的消息, 滚动摘要文本或 None)。"""
    if not messages:
        return [], None
    total = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
    if total <= max_tokens:
        return messages, None
    kept = [messages[0]] + messages[-keep_recent:]
    dropped = messages[1:-keep_recent] if len(messages) > keep_recent + 1 else []
    summary = "\n".join(
        f"[{m.get('role')}] {str(m.get('content', ''))[:200]}" for m in dropped
    )
    return kept, summary or None
