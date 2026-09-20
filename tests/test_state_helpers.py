"""state 助手单测：消息文本提取(dict 与 LangChain 消息对象兼容)。"""
from maintainer_copilot.graph.state import message_text


def test_message_text_from_dict() -> None:
    assert message_text({"role": "user", "content": "你好"}) == "你好"


def test_message_text_from_langchain_object() -> None:
    class FakeMessage:
        content = "对象消息"

    assert message_text(FakeMessage()) == "对象消息"


def test_message_text_multimodal_blocks() -> None:
    class FakeMessage:
        content = [{"type": "text", "text": "第一块"}, {"type": "text", "text": "第二块"}]

    assert message_text(FakeMessage()) == "第一块 第二块"
