"""ReviewWorker 单测：diff 解析 / 初审生成 / 截断降级 / 缺 PR 编号拒绝。"""
from types import SimpleNamespace

import pytest

from maintainer_copilot.workers.reviewer import (
    DIFF_HARD_LIMIT,
    ReviewWorker,
    parse_diff_files,
    reviewer_node,
)

SAMPLE_DIFF = """diff --git a/src/a.py b/src/a.py
index 111..222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,4 @@
 def foo():
+    return 1
diff --git a/docs/b.md b/docs/b.md
new file mode 100644
index 000..333
--- /dev/null
+++ b/docs/b.md
@@ -0,0 +1 @@
+new doc
"""


def test_review_prompt_forbids_uncited_facts() -> None:
    """回归防线: 初审 Prompt 必须含"禁止引用外事实"硬规则(E2E 抓出的引用编造)。"""
    from maintainer_copilot.workers.reviewer import REVIEW_PROMPT

    assert "严禁引用之外的具体事实" in REVIEW_PROMPT
    assert "不得把资料里没有的细节" in REVIEW_PROMPT


def test_parse_diff_files() -> None:
    assert parse_diff_files(SAMPLE_DIFF) == ["src/a.py", "docs/b.md"]
    assert parse_diff_files("") == []


class _FakeGitHub:
    def __init__(self, diff: str) -> None:
        self.diff = diff

    async def get_pull_request(self, repo: str, number: int) -> dict:
        return {"number": number, "title": "feat: demo", "body": "说明"}

    async def get_pull_diff(self, repo: str, number: int) -> str:
        return self.diff


class _FakeRetriever:
    async def retrieve(self, q, repo, top_k=6):
        return [
            {"id": "d1", "source": "code", "path": "src/a.py", "text": "既有实现", "rerank_score": 0.9}
        ]


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(content="意见: foo 处需注意 [1]")


@pytest.mark.asyncio
async def test_review_drafts_with_citations() -> None:
    llm = _FakeLLM()
    worker = ReviewWorker(  # type: ignore[arg-type]
        llm=llm, retriever=_FakeRetriever(), github=_FakeGitHub(SAMPLE_DIFF)
    )
    r = await worker.review("freeisle/ragent", 1)
    assert "注意" in r["draft"]
    assert r["citations"][0]["path"] == "src/a.py"
    assert r["pr_meta"]["issue_number"] == 1
    assert r["pr_meta"]["changed_files"] == ["src/a.py", "docs/b.md"]
    assert r["summary_mode"] is False
    prompt = llm.calls[0][0]["content"]
    assert "src/a.py" in prompt and "docs/b.md" in prompt


@pytest.mark.asyncio
async def test_review_refuses_when_diff_empty() -> None:
    """diff 拉取失败/为空: 明确拒绝, 不生成误导性草稿。"""
    worker = ReviewWorker(  # type: ignore[arg-type]
        llm=_FakeLLM(), retriever=_FakeRetriever(), github=_FakeGitHub("")
    )
    r = await worker.review("freeisle/ragent", 1)
    assert "依据不足" in r["draft"]
    assert r["citations"] == []


@pytest.mark.asyncio
async def test_review_truncates_oversized_diff_to_summary_mode() -> None:
    llm = _FakeLLM()
    big_diff = SAMPLE_DIFF + "\n+ padding " * (DIFF_HARD_LIMIT // 10)
    worker = ReviewWorker(  # type: ignore[arg-type]
        llm=llm, retriever=_FakeRetriever(), github=_FakeGitHub(big_diff)
    )
    r = await worker.review("freeisle/ragent", 2)
    assert r["summary_mode"] is True
    prompt = llm.calls[0][0]["content"]
    assert "只做文件级总结" in prompt
    assert len(prompt) < DIFF_HARD_LIMIT + 4000  # 截断生效


@pytest.mark.asyncio
async def test_reviewer_node_without_pr_number_refuses() -> None:
    out = await reviewer_node({"repo": "freeisle/ragent", "task_type": "review"})  # type: ignore[arg-type]
    assert "依据不足" in out["draft"]
    assert out["citations"] == []


def test_agent_state_schema_includes_pr_field() -> None:
    """回归测试: pr 键必须在 AgentState schema 内。

    E2E 抓出的真 bug: 初始 state 里的 pr 键不在 schema 中, LangGraph 静默丢弃,
    reviewer_node 拿不到 PR 编号, 初审恒为"依据不足"拒绝话术。
    """
    from maintainer_copilot.graph.state import AgentState

    assert "pr" in AgentState.__annotations__
