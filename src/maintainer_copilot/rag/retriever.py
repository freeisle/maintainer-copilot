"""混合检索：精确串 + 稠密(pgvector) + 稀疏(BM25) 三通道, RRF 融合后 rerank。

通道设计:
- 精确串: 报错信息等特征串的 LIKE 查询, 检索指纹; 三通道 RRF 等权融合, 精确通道靠多 issue 叠加命中生效
- 稠密: pgvector HNSW cosine, bge-m3 向量
- 稀疏: 内存 BM25(rank_bm25), 按 repo 惰性构建并缓存
RRF 自实现, 不依赖第三方:
score(d) = Σ 1/(k + rank_i(d)), k=60(经验值, 对排名扰动鲁棒)。
"""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from ..config import get_settings
from .embedder import Embedder
from .indexer import Vector
from .query_rewriter import extract_error_strings
from .reranker import Reranker
from .lexical import LexicalIndex, diversify, tokenize
from .dedup import deduplicate_docs

logger = logging.getLogger(__name__)

RRF_K = 60
_FETCH_COLS = "id, content, source, path, meta"


def rrf_fuse(
    ranked_lists: list[list[str]], k: int = RRF_K, top_n: int | None = None
) -> list[tuple[str, float]]:
    """融合多个排名列表(元素为 doc_id), 返回按融合分降序的 (doc_id, score)。"""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(dict.fromkeys(ranked)):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return fused[:top_n] if top_n else fused


def _tokenize(text: str) -> list[str]:
    """简单分词: 英文按单词, 中文逐字。"""
    return tokenize(text)


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
        self._bm25: dict[str, LexicalIndex] = {}

    async def _conn(self):
        import psycopg
        from pgvector.psycopg import register_vector_async

        conn = await psycopg.AsyncConnection.connect(self.database_url, connect_timeout=5)
        try:
            await register_vector_async(conn)
        except BaseException:
            await conn.close()
            raise
        return conn

    async def _dense(self, repo: str, query: str, k: int, scores: dict | None = None) -> list[str]:
        vec = Vector(self.embedder.embed_query(query))
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT id, 1 - (embedding <=> %s) FROM chunks WHERE repo = %s AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s LIMIT %s",
                (vec, repo, vec, k),
            )
            rows = await cur.fetchall()
            if scores is not None:
                scores.update({r[0]: float(r[1]) for r in rows})
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

    async def _sparse(self, repo: str, query: str, k: int, scores: dict | None = None) -> list[str]:
        """BM25 词法通道: 惰性构建内存索引, 按 repo 缓存。"""
        if repo not in self._bm25:
            logger.info("构建 BM25 索引: %s", repo)
            conn = await self._conn()
            try:
                cur = await conn.execute(f"SELECT {_FETCH_COLS} FROM chunks WHERE repo = %s ORDER BY id", (repo,))
                rows = await cur.fetchall()
            finally:
                await conn.close()
            if not rows:
                return []
            docs = [dict(id=r[0], text=r[1], source=r[2], path=r[3], meta=r[4], repo=repo) for r in rows]
            self._bm25[repo] = LexicalIndex(docs)
        results = self._bm25[repo].search(query, k)
        if scores is not None:
            scores.update({r["id"]: r["rerank_score"] for r in results})
        return [row["id"] for row in results]

    async def _fetch(self, repo: str, ids: list[str]) -> list[dict]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                f"SELECT {_FETCH_COLS} FROM chunks WHERE repo = %s AND id = ANY(%s)", (repo, ids)
            )
            rows = await cur.fetchall()
            by_id = {
                r[0]: {"id": r[0], "text": r[1], "source": r[2], "path": r[3], "meta": r[4], "repo": repo}
                for r in rows
            }
            return [by_id[i] for i in ids if i in by_id]
        finally:
            await conn.close()

    async def retrieve(self, query: str, repo: str, top_k: int = 8, rerank_pool: int = 50,
                       *, trace: dict | None = None) -> list[dict]:
        """Retrieve with optional per-call diagnostics; never retain trace on the instance.

        Trace contains identifiers/scores, not chunk contents. Channel positions refer
        to the entire requested pool, not to the final top-k. Errors propagate.
        """
        start = perf_counter()
        dense_scores, sparse_scores, timings = {}, {}, {}

        async def timed(name, call):
            t = perf_counter()
            try:
                return await call
            finally:
                timings[name] = perf_counter() - t

        exact_ids, dense_ids, sparse_ids = await asyncio.gather(
            timed("exact", self._exact(repo, query, 20)),
            timed("dense", self._dense(repo, query, rerank_pool, dense_scores)
                  if trace is not None else self._dense(repo, query, rerank_pool)),
            timed("sparse", self._sparse(repo, query, rerank_pool, sparse_scores)
                  if trace is not None else self._sparse(repo, query, rerank_pool)),
        )
        t = perf_counter()
        all_fused = rrf_fuse([exact_ids, dense_ids, sparse_ids], k=RRF_K)
        fused = all_fused[:rerank_pool]
        timings["fusion"] = perf_counter() - t
        docs = deduplicate_docs(await self._fetch(repo, [i for i, _ in fused])) if fused else []
        before = list(docs)
        t = perf_counter()
        if self.reranker is not None:
            docs = self.reranker.rerank(query, docs)
        timings["rerank"] = perf_counter() - t
        final = diversify(docs, top_k)
        timings["total"] = perf_counter() - start
        if trace is not None:
            # Extra fetch is diagnostic I/O only; no second embedding/model prediction.
            union = await self._fetch(repo, [i for i, _ in all_fused]) if all_fused else []
            def brief(d):
                return {key: d[key] for key in ("id", "repo", "path", "source", "meta", "rerank_score") if key in d}
            trace.update(query=query, normalized_tokens=tokenize(query),
                         exact_patterns=extract_error_strings(query), repo=repo,
                         channels={"exact": exact_ids, "dense": dense_ids, "sparse": sparse_ids},
                         dense_scores=dense_scores, bm25_scores=sparse_scores,
                         fused=[{"id": i, "score": score} for i, score in all_fused],
                         candidates=[brief(d) for d in union],
                         before_rerank=[brief(d) for d in before],
                         after_rerank=[brief(d) for d in docs],
                         final=[brief(d) for d in final], timings=timings,
                         limits={"exact": 20, "dense": rerank_pool, "sparse": rerank_pool,
                                 "rerank_pool": rerank_pool, "top_k": top_k})
        return final
