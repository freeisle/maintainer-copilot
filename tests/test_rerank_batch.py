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
