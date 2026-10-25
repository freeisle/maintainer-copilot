import pytest
from eval.score_cache import CachedReranker
from maintainer_copilot.rag.reranker import Reranker


def test_actual_score_cache_content_revision_and_stable_ties(tmp_path, monkeypatch):
    calls = []
    def predict(self, query, candidates, top_k=None):
        calls.append((query, [d["text"] for d in candidates]))
        return [{**d, "rerank_score": .5} for d in candidates]
    monkeypatch.setattr(Reranker, "rerank", predict)
    cache = tmp_path / "scores.json"
    r = CachedReranker(cache, "revision-a")
    rows = [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}]
    assert [d["id"] for d in r.rerank("q", rows)] == ["a", "b"]
    second = CachedReranker(cache, "revision-a")
    assert [d["id"] for d in second.rerank("q", rows[::-1])] == ["b", "a"]
    assert len(calls) == 1
    second.rerank("q", [{"id": "a", "text": "changed"}])
    CachedReranker(cache, "revision-b").rerank("q", rows)
    assert len(calls) == 3
    assert second.hits == 2 and second.misses == 1


def test_failed_model_does_not_write_cache(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("failed")
    monkeypatch.setattr(Reranker, "rerank", fail)
    cache = tmp_path / "scores.json"
    with pytest.raises(RuntimeError):
        CachedReranker(cache, "revision").rerank("q", [{"id": "a", "text": "text"}])
    assert not cache.exists()
