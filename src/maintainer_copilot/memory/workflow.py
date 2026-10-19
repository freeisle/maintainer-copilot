"""Bound and replace graph history at each new turn, before routing."""
from langchain_core.messages import RemoveMessage
import uuid
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from ..config import get_settings
from ..graph.state import message_text
from .short_term import trim_messages


def as_dict(message):
    if isinstance(message, dict):
        return {"role": message.get("role", "user"), "content": message_text(message)}
    role = {"human": "user", "ai": "assistant"}.get(message.type, message.type)
    return {"role": role, "content": message_text(message)}


def memory_node(state):
    repo = state.get("repo", "")
    if state.get("memory_repo", repo) != repo:
        raise ValueError("A conversation thread cannot switch repositories; use a new thread_id")
    messages, _ = trim_messages([as_dict(m) for m in state.get("messages", [])],
                               max_tokens=get_settings().memory_max_tokens, strict_budget=True)
    return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages],
            "turn_id": uuid.uuid4().hex,
            "memory_repo": repo, "approval": "pending", "final_draft": "",
            "final_action": {}, "rewrite_count": 0, "reflect_advice": "", "reflection": {}}
