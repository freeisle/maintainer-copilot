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
    messages: list[dict], max_tokens: int = 4000, keep_recent: int = 6
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
    dropped = messages[1:-keep_recent] if len(messages) > keep_recent + 1 else []
    summary = "\n".join(
        f"[{m.get('role')}] {str(m.get('content', ''))[:200]}" for m in dropped
    )
    kept = [messages[0]]
    if summary:
        kept.append({"role": "system", "content": SUMMARY_PREFIX + "\n" + summary})
    kept.extend(messages[-keep_recent:])
    return kept, summary or None
