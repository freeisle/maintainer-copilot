"""Batch configuration preserves ordering/top-k; no model-quality claim."""
import pytest
from maintainer_copilot.rag.reranker import Reranker


def test_rerank_batch_configuration_preserves_scores_and_order():
    class Encoder:
        def predict(self, pairs, *, batch_size):
            assert batch_size == 4
            assert pairs == [('q', 'first'), ('q', 'second')]
            return [0.1, 0.9]

    reranker = Reranker(batch_size=4)
    reranker._encoder = Encoder()
    docs = [{'id': 'a', 'text': 'first'}, {'id': 'b', 'text': 'second'}]
    assert reranker.rerank('q', docs, top_k=1) == [{'id': 'b', 'text': 'second', 'rerank_score': 0.9}]
    assert Reranker().batch_size == 32
    with pytest.raises(ValueError):
        Reranker(batch_size=0)


@pytest.mark.parametrize("scores", [[], [float("nan")], [float("inf")]])
def test_invalid_model_scores_are_not_silently_ranked(scores):
    class Encoder:
        def predict(self, *args, **kwargs):
            return scores
    r = Reranker()
    r._encoder = Encoder()
    with pytest.raises(ValueError, match="incomplete or non-finite"):
        r.rerank("q", [{"id": "a", "text": "a"}])


def test_pinned_model_loads_once(monkeypatch):
    import sys
    from types import SimpleNamespace
    from maintainer_copilot.rag.embedder import Embedder
    calls = []
    def factory(model, **kwargs):
        calls.append((model, kwargs))
        return object()
    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(CrossEncoder=factory, SentenceTransformer=factory))
    r = Reranker(revision="fixed-reranker")
    e = Embedder(revision="fixed-embedding")
    assert r._load() is r._load()
    assert e._load_local() is e._load_local()
    assert [c[1]["revision"] for c in calls] == ["fixed-reranker", "fixed-embedding"]
