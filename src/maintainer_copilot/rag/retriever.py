"""混合检索：精确串 + 稠密(pgvector) + 稀疏(BM25) 三通道, RRF 融合后 rerank。

通道设计:
- 精确串: 报错信息等特征串的 LIKE 查询, 检索指纹, 融合时排最前(权重最高)
- 稠密: pgvector HNSW cosine, bge-m3 向量
- 稀疏: 内存 BM25(rank_bm25), 按 repo 惰性构建并缓存
RRF 自实现, 不依赖第三方:
score(d) = Σ 1/(k + rank_i(d)), k=60(经验值, 对排名扰动鲁棒)。
"""
from __future__ import annotations

import asyncio
import logging
import re

from rank_bm25 import BM25Okapi

from ..config import get_settings
from .embedder import Embedder
from .indexer import Vector
from .query_rewriter import extract_error_strings
from .reranker import Reranker

logger = logging.getLogger(__name__)

RRF_K = 60
_FETCH_COLS = "id, content, source, path, meta"


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


def _tokenize(text: str) -> list[str]:
    """简单分词: 英文按单词, 中文逐字。"""
    return re.findall(r"[a-zA-Z0-9_]+|[一-鿿]", text.lower())


class HybridRetriever:
    def __init__(
        self,
        database_url: str | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.database_url = database_url or get_settings().database_url
        self.embedder = embedder or Embedder()
        self.reranker = reranker or Reranker()
        self._bm25: dict[str, tuple[list[str], BM25Okapi]] = {}

    async def _conn(self):
        import psycopg
        from pgvector.psycopg import register_vector_async

        conn = await psycopg.AsyncConnection.connect(self.database_url)
        await register_vector_async(conn)
        return conn

    async def _dense(self, repo: str, query: str, k: int) -> list[str]:
        vec = Vector(self.embedder.embed_query(query))
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT id FROM chunks WHERE repo = %s AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s LIMIT %s",
                (repo, vec, k),
            )
            rows = await cur.fetchall()
            return [r[0] for r in rows]
        finally:
            await conn.close()

    async def _exact(self, repo: str, query: str, k: int) -> list[str]:
        """报错串精确通道: LIKE 查询, 命中即强证据。"""
        errs = extract_error_strings(query)
        if not errs:
            return []
        conn = await self._conn()
        try:
            ids: list[str] = []
            for err in errs[:3]:
                cur = await conn.execute(
                    "SELECT id FROM chunks WHERE repo = %s AND content LIKE %s LIMIT %s",
                    (repo, f"%{err[:60]}%", k),
                )
                rows = await cur.fetchall()
                ids.extend(r[0] for r in rows)
            return ids
        finally:
            await conn.close()

    async def _sparse(self, repo: str, query: str, k: int) -> list[str]:
        """BM25 词法通道: 惰性构建内存索引, 按 repo 缓存。"""
        if repo not in self._bm25:
            logger.info("构建 BM25 索引: %s", repo)
            conn = await self._conn()
            try:
                cur = await conn.execute("SELECT id, content FROM chunks WHERE repo = %s", (repo,))
                rows = await cur.fetchall()
            finally:
                await conn.close()
            if not rows:
                return []
            ids = [r[0] for r in rows]
            corpus = [_tokenize(r[1]) for r in rows]
            self._bm25[repo] = (ids, BM25Okapi(corpus))
        ids, index = self._bm25[repo]
        scores = index.get_scores(_tokenize(query))
        ranked = sorted(zip(ids, scores), key=lambda item: item[1], reverse=True)[:k]
        return [doc_id for doc_id, score in ranked if score > 0]

    async def _fetch(self, repo: str, ids: list[str]) -> list[dict]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                f"SELECT {_FETCH_COLS} FROM chunks WHERE id = ANY(%s)", (ids,)
            )
            rows = await cur.fetchall()
            by_id = {
                r[0]: {"id": r[0], "text": r[1], "source": r[2], "path": r[3], "meta": r[4]}
                for r in rows
            }
            return [by_id[i] for i in ids if i in by_id]
        finally:
            await conn.close()

    async def retrieve(self, query: str, repo: str, top_k: int = 8, rerank_pool: int = 50) -> list[dict]:
        """混合检索主入口: 三通道并行 -> RRF -> rerank -> top_k。"""
        exact_ids, dense_ids, sparse_ids = await asyncio.gather(
            self._exact(repo, query, 20),
            self._dense(repo, query, rerank_pool),
            self._sparse(repo, query, rerank_pool),
        )
        fused = rrf_fuse([exact_ids, dense_ids, sparse_ids], k=RRF_K, top_n=rerank_pool)
        if not fused:
            return []
        docs = await self._fetch(repo, [doc_id for doc_id, _ in fused])
        if self.reranker is not None:
            return self.reranker.rerank(query, docs, top_k)
        return docs[:top_k]
