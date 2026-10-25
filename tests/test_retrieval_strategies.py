from unittest.mock import AsyncMock
import pytest
from maintainer_copilot.rag.lexical import query_symbols, symbol_search, blend_document_ranks, LexicalIndex
from maintainer_copilot.rag.retriever import HybridRetriever
from maintainer_copilot.workers.solver import merge_results


def row(ident, path, text="", repo="r"):
    return dict(id=ident, path=path, text=text, repo=repo, source="code", meta={})


def test_symbols_boundaries_paths_and_language_neutrality():
    query = "chooseSeats 为什么抛 StringIndexOutOfBoundsException？ build_idempotency_key"
    assert query_symbols(query) == ["chooseSeats", "StringIndexOutOfBoundsException", "build_idempotency_key"]
    assert query_symbols("How does retrieval work?") == []
    rows = [row("a", "other.py", "chooseSeats StringIndexOutOfBoundsException"),
            row("b", "chooseSeats.java", ""), row("c", "c.java", "chooseSeatsExtra"),
            row("d", "a.py", "build_idempotency_key")]
    found = symbol_search(rows, query, 20)
    assert [r["id"] for r in found] == ["a", "b", "d"]
    assert len(found[0]["symbol_matches"]) == 2
    assert symbol_search(rows, "no explicit symbols", 20) == []


def test_rank_blend_preserves_scores_stability_and_solver_order():
    before = [row("a", "a.py"), row("b", "b.py"), row("c", "a.py")]
    after = [{**before[1], "rerank_score": 0.9}, {**before[0], "rerank_score": 0.1},
             {**before[2], "rerank_score": 0.05}]
    blended = blend_document_ranks(before, after)
    assert [d["id"] for d in blended] == ["b", "a", "c"]  # tied document scores retain CE order
    assert [d["rerank_score"] for d in blended] == [0.9, 0.1, 0.05]
    assert merge_results([blended]) == blended
    assert blend_document_ranks([], []) == []
    # A distinct repo/path pair must never share document rank.
    isolated = [row("x", "a.py", repo="other"), before[0]]
    assert len({d["ranking_score"] for d in blend_document_ranks(isolated, isolated)}) == 2


@pytest.mark.asyncio
async def test_coverage_pool_uses_same_budget_and_keeps_missing_document():
    r = HybridRetriever(coverage_pool=True, symbol_recall=True)
    r.reranker = None
    rows = [row("a", "a.py", "loadValue"), row("b", "a.py", "different"), row("c", "c.py", "loadValue")]
    r._bm25["r"] = LexicalIndex(rows)
    r._exact = AsyncMock(return_value=[])
    r._dense = AsyncMock(return_value=["a", "b", "c"])
    r._sparse = AsyncMock(return_value=["a", "b"])
    async def fetch(repo, ids):
        return [next(d for d in rows if d["id"] == i) for i in ids]
    r._fetch = fetch
    trace = {}
    result = await r.retrieve("loadValue", "r", rerank_pool=2, trace=trace)
    assert len(trace["before_rerank"]) == 2
    assert {d["path"] for d in result} == {"a.py", "c.py"}
    assert trace["channels"]["symbol"] == ["a", "c"]
    r.coverage_pool = r.symbol_recall = False
    result = await r.retrieve("loadValue", "r", rerank_pool=2)
    assert {d["path"] for d in result} == {"a.py"}


@pytest.mark.asyncio
async def test_model_failure_propagates_without_fabricated_trace():
    r = HybridRetriever(rank_blend=True)
    r._exact = AsyncMock(return_value=[])
    r._dense = AsyncMock(return_value=["a"])
    r._sparse = AsyncMock(return_value=[])
    r._fetch = AsyncMock(return_value=[row("a", "a.py")])
    class Broken:
        def rerank(self, *args):
            raise RuntimeError("model unavailable")
    r.reranker = Broken()
    trace = {}
    with pytest.raises(RuntimeError, match="model unavailable"):
        await r.retrieve("q", "r", trace=trace)
    assert trace == {}
