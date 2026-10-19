"""短期记忆：会话消息的滑动窗口裁剪 + 滚动摘要 + 任务锚点。

- 按 token 预算裁剪(中文按字符粗估, 英文按 4 字符 1 token)
- 超出预算时: 保留首条(任务锚点, 永不丢) + 最近 keep_recent 条,
  中间部分压缩为滚动摘要, 以 system 消息插回锚点之后
- 摘要带前缀标注"系统生成, 非原话", 防止后续被当作用户原话引用
"""
import re

_CJK = re.compile(r"[一-鿿]")

SUMMARY_PREFIX = "【对话摘要(系统生成, 非原话)】"


def estimate_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + other // 4


def trim_messages(
    messages: list[dict], max_tokens: int = 4000, keep_recent: int = 6, *, strict_budget: bool = False
) -> tuple[list[dict], str | None]:
    """返回 (裁剪后的消息列表, 滚动摘要文本或 None)。

    裁剪后结构: [锚点(首条), system 摘要, ...最近 keep_recent 条]。
    摘要作为消息插入列表, 下一轮裁剪时会被再次卷入摘要(滚动效果)。
    """
    if not messages:
        return [], None
    total = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
    if total <= max_tokens:
        return messages, None
    if max_tokens < 64 or keep_recent < 1:
        raise ValueError("max_tokens must be >=64 and keep_recent >=1")

    def bounded(text, budget):
        text = str(text)
        while estimate_tokens(text) > budget:
            text = text[:max(0, len(text) * 3 // 4)]
        return text

    # A single oversized message cannot be preserved in full under a hard budget.
    anchor = {**messages[0], "content": bounded(messages[0].get("content", ""), max_tokens // 3)} if strict_budget else dict(messages[0])
    recent = [dict(m) for m in messages[max(1, len(messages) - keep_recent):]]
    recent_budget = max_tokens // 2
    per_message = max(1, recent_budget // max(1, len(recent)))
    if strict_budget:
        for m in recent:
            m["content"] = bounded(m.get("content", ""), per_message)
    dropped = messages[1:max(1, len(messages) - keep_recent)]
    pieces = []
    for m in dropped:
        content = str(m.get("content", ""))
        if content.startswith(SUMMARY_PREFIX):
            pieces.append(content[len(SUMMARY_PREFIX):].strip())
        else:
            pieces.append(f"[{m.get('role')}] {content[:200]}")
    budget = max_tokens - sum(estimate_tokens(m["content"]) for m in [anchor, *recent]) if strict_budget else max_tokens // 3
    summary = bounded("\n".join(pieces), max(0, budget - estimate_tokens(SUMMARY_PREFIX) - 1))
    kept = [anchor]
    if summary:
        kept.append({"role": "system", "content": SUMMARY_PREFIX + "\n" + summary})
    return kept + recent, summary or None
