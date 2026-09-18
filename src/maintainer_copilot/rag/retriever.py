"""混合检索：稠密向量(pgvector) + 稀疏(BM25) + rerank 的统一入口。

RRF(Reciprocal Rank Fusion) 自实现, 不依赖第三方:
score(d) = Σ 1/(k + rank_i(d)), k=60(经验值, 对排名扰动鲁棒, 面试可讲公式与权衡)。
"""
from __future__ import annotations

RRF_K = 60


def rrf_fuse(
    ranked_lists: list[list[str]], k: int = RRF_K, top_n: int | None = None
) -> list[tuple[str, float]]:
    """融合多个排名列表(元素为 doc_id), 返回按融合分降序的 (doc_id, score)。"""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return fused[:top_n] if top_n else fused


class HybridRetriever:
    """TODO(D4): pgvector 向量查询 + BM25 索引 + bge-reranker 重排。

    返回结构: [{"chunk_id", "text", "source", "meta", "score"}]
    """

    async def retrieve(self, query: str, repo: str, top_k: int = 8) -> list[dict]:
        raise NotImplementedError("Sprint 1 D4 实现")
