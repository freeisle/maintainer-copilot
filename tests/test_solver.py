"""SolverWorker 单测：引用解析 / 多轮检索合并 / Answer Prompt 硬规则 / 无资料拒绝。"""
from types import SimpleNamespace

import pytest

from maintainer_copilot.workers.solver import (
    REFUSAL_TEXT,
    SolverWorker,
    build_answer_prompt,
    merge_results,
    parse_citations,
)


def test_parse_citations_dedup_and_order() -> None:
    assert parse_citations("结论一 [1] 结论二 [2][1] 结论三 [3]") == [1, 2, 3]
    assert parse_citations("没有引用的回答") == []


def test_parse_citations_ignores_invalid() -> None:
    # 解析层只取数字; 编号有效性由 solve() 校验
    assert parse_citations("[99]") == [99]


def test_merge_results_dedupe_keep_max_score() -> None:
    batches = [
        [{"id": "a", "rerank_score": 0.8}, {"id": "b", "rerank_score": 0.6}],
        [{"id": "a", "rerank_score": 0.9}, {"id": "c", "rerank_score": 0.5}],
    ]
    merged = merge_results(batches)
    assert [m["id"] for m in merged] == ["a", "b", "c"]
    assert merged[0]["rerank_score"] == 0.9  # a 保留最高分
    assert len(merged) == 3  # 按 id 去重


def test_answer_prompt_forbids_uncited_facts() -> None:
    """回归防线: Prompt 必须含"禁止引用外具体事实 + 固定拒绝话术"硬规则。"""
    text = build_answer_prompt("freeisle/ragent", "[1] (code:a.py) ctx", "怎么配置?")
    assert "严禁输出引用之外的具体事实" in text
    assert "紧跟引用编号 [n]" in text
    assert REFUSAL_TEXT in text


class _FakeRetriever:
    async def retrieve(self, q, repo, top_k=8):
        return []


class _FakeLLM:
    async def chat(self, messages, **kwargs):
        return SimpleNamespace(content="")


@pytest.mark.asyncio
async def test_solve_refuses_when_no_docs() -> None:
    worker = SolverWorker(llm=_FakeLLM(), retriever=_FakeRetriever())  # type: ignore[arg-type]
    r = await worker.solve("怎么配置?", "freeisle/ragent")
    assert r["draft"] == REFUSAL_TEXT
    assert r["citations"] == []
    assert r["docs"] == []


class _DocRetriever:
    async def retrieve(self, q, repo, top_k=8):
        return [{"id": "d1", "source": "doc", "path": "p.md", "text": "资料内容", "rerank_score": 0.9}]


class _CapturingLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(content=self.content)


@pytest.mark.asyncio
async def test_solve_injects_reply_language_preference() -> None:
    llm = _CapturingLLM("回答 [1]")
    worker = SolverWorker(  # type: ignore[arg-type]
        llm=llm,
        retriever=_DocRetriever(),  # type: ignore[arg-type]
        prefs_getter=lambda repo, key, default=None: "en",
    )
    r = await worker.solve("问?", "freeisle/ragent")
    answer_prompt = llm.calls[-1][0]["content"]  # 最后一通 LLM 调用是回答
    assert "回复必须使用语言: en" in answer_prompt
    assert r["citations"][0]["path"] == "p.md"


@pytest.mark.asyncio
async def test_solve_without_preference_has_no_language_note() -> None:
    llm = _CapturingLLM("回答 [1]")
    worker = SolverWorker(  # type: ignore[arg-type]
        llm=llm,
        retriever=_DocRetriever(),  # type: ignore[arg-type]
        prefs_getter=lambda repo, key, default=None: None,
    )
    await worker.solve("问?", "freeisle/ragent")
    assert "偏好要求" not in llm.calls[-1][0]["content"]
