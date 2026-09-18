"""RRF 融合单测(纯函数)。"""
from maintainer_copilot.rag.retriever import rrf_fuse


def test_rrf_fusion_ordering() -> None:
    # a 在 list1 第 1、list2 第 3; b 在 list1 第 2、list2 第 1 -> b 总分高于 a
    fused = rrf_fuse([["a", "b"], ["b", "c", "a"]], k=60)
    assert [doc for doc, _ in fused] == ["b", "a", "c"]


def test_rrf_fusion_top_n() -> None:
    fused = rrf_fuse([["a", "b", "c"]], k=60, top_n=2)
    assert [doc for doc, _ in fused] == ["a", "b"]


def test_rrf_fusion_empty() -> None:
    assert rrf_fuse([]) == []
