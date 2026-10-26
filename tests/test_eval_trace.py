import pytest
from eval.run_eval import search_with_trace, channel_hits_from_trace


@pytest.mark.asyncio
async def test_eval_uses_actual_trace_without_second_embedding():
    class Retriever:
        def __init__(self):
            self.calls = []
        async def retrieve(self, query, repo, top_k, trace):
            self.calls.append(query)
            docs = [dict(id="a", path="a.py", source="code", repo=repo, rerank_score=.9)]
            trace.update(candidates=docs, channels={"dense": ["a"], "sparse": [], "exact": []})
            return docs
    r = Retriever()
    keys, traces = await search_with_trace(r, ["original", "rewrite"], "r", 8)
    assert r.calls == ["original", "rewrite"]
    assert keys == ["a.py"]
    assert channel_hits_from_trace(traces[0], {"a.py"}, 8) == {
        "dense": True, "dense_documents": ["a.py"], "sparse": False,
        "sparse_documents": [], "exact": False, "exact_documents": []}


def test_channel_diagnostic_respects_cutoff():
    trace = {"candidates": [{"id": "a", "path": "a.py"}, {"id": "b", "path": "b.py"}],
             "channels": {"dense": ["a", "b"], "sparse": [], "exact": []}}
    assert not channel_hits_from_trace(trace, {"b.py"}, 1)["dense"]
    assert channel_hits_from_trace(trace, {"b.py"}, 2)["dense"]


@pytest.mark.asyncio
async def test_empty_document_key_keeps_original_eval_filtering():
    class Retriever:
        async def retrieve(self, *args, **kwargs):
            return [{"id": "", "path": "", "source": "code"}]
    keys, _ = await search_with_trace(Retriever(), ["q"], "r", 8)
    assert keys == []
