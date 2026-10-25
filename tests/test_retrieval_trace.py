from unittest.mock import AsyncMock
import pytest
from maintainer_copilot.rag.retriever import HybridRetriever
from eval.diagnose_retrieval import relevant_positions


@pytest.mark.asyncio
async def test_trace_preserves_results_and_full_pool():
    r = HybridRetriever()
    r.reranker = None
    r._exact = AsyncMock(return_value=[])
    r._dense = AsyncMock(return_value=["a", "b", "c"])
    r._sparse = AsyncMock(return_value=["b", "d"])
    rows = {x: dict(id=x, path=x, repo="r", source="code", text=x) for x in "abcd"}
    async def fetch(repo, ids):
        return [rows[i] for i in ids]
    r._fetch = fetch
    original = await r.retrieve("query", "r", top_k=1, rerank_pool=2)
    trace = {}
    assert await r.retrieve("query", "r", top_k=1, rerank_pool=2, trace=trace) == original
    assert len(trace["candidates"]) == 4
    assert len(trace["before_rerank"]) == 2
    assert r._dense.await_count == 2  # one per call, not a second diagnostic embedding
    positions = relevant_positions(trace, {"c", "missing", "b"})
    assert positions["c"]["lost_stage"] == "fusion_cutoff"
    assert positions["missing"]["lost_stage"] == "outside_requested_channel_pools"
    assert positions["b"]["lost_stage"] is None


@pytest.mark.asyncio
async def test_empty_and_error_are_not_successful_diagnostics():
    r = HybridRetriever()
    r._exact = AsyncMock(return_value=[])
    r._dense = AsyncMock(return_value=[])
    r._sparse = AsyncMock(return_value=[])
    trace = {}
    assert await r.retrieve("q", "r", trace=trace) == []
    assert trace["candidates"] == []
    r._dense.side_effect = TimeoutError("database timeout")
    with pytest.raises(TimeoutError):
        await r.retrieve("q", "r", trace={})
