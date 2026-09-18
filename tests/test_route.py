"""Route 节点规则路由单测。"""
import pytest

from maintainer_copilot.graph.route import route_by_rules, route_node


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("怎么配置数据源?", "solve"),
        ("issue #123 提到的 bug 复现了", "triage"),
        ("请 review 这个 pull request", "review"),
        ("这个 PR 的 CI 挂了", "review"),
        ("中文提问: 如何安装?", "solve"),
    ],
)
def test_route_by_rules(text: str, expected: str) -> None:
    assert route_by_rules(text) == expected


@pytest.mark.asyncio
async def test_route_node_reads_last_message() -> None:
    state = {"messages": [{"role": "user", "content": "issue #5 报错"}]}
    out = await route_node(state)
    assert out["task_type"] == "triage"


@pytest.mark.asyncio
async def test_route_node_respects_preset_task_type() -> None:
    state = {"task_type": "solve", "messages": [{"role": "user", "content": "issue #5"}]}
    out = await route_node(state)
    assert out["task_type"] == "solve"
