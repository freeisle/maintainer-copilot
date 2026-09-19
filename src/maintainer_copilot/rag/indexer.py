"""入库管线：chunk -> embed -> pgvector upsert，按 commit SHA 版本化。

设计要点:
- 逻辑主键: repo|source|path|heading 的 hash —— 同一逻辑位置内容变化才重算 embedding
- 批量嵌入: 先批量查出未变化的 chunk 跳过, 剩余一次性 encode(CPU 上 bge-m3 批量比逐条快数倍)
- 增量更新: 变更文件 re-chunk 后按 (repo, path) 删除重插(delete_by_path)
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .chunkers import Chunk

logger = logging.getLogger(__name__)

try:
    from pgvector.psycopg import Vector  # pgvector < 0.4 路径
except ImportError:  # pragma: no cover - 版本分支
    from pgvector.psycopg.vector import Vector  # pgvector >= 0.4 路径

DIM = 1024  # bge-m3 输出维度

DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL,
    commit_sha TEXT,
    embedding vector(1024)
);
CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks (repo);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks (source);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING hnsw (embedding vector_cosine_ops);
"""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def chunk_id(repo: str, chunk: Chunk) -> str:
    """逻辑主键: 同一逻辑位置(repo+来源+位置描述)的内容变化时覆盖更新。"""
    return content_hash("|".join([repo, chunk.source, _chunk_location(chunk)]))


def _chunk_location(chunk: Chunk) -> str:
    """按源类型生成位置描述: issue 用编号+部分, 代码用符号+行号+分段, 文档用路径+标题+分段。"""
    if chunk.source == "issue":
        return f"issue#{chunk.meta.get('issue', '')}:{chunk.meta.get('part', '')}"
    if chunk.source == "code":
        return (
            f"{chunk.meta.get('path', '')}#{chunk.meta.get('symbol', '')}"
            f"#{chunk.meta.get('start', '')}#{chunk.meta.get('part_idx', 0)}"
        )
    return (
        f"{chunk.meta.get('path', '')}#{chunk.meta.get('heading', '')}"
        f"#{chunk.meta.get('part_idx', 0)}"
    )


class Indexer:
    def __init__(self, database_url: str, embedder: Any = None) -> None:
        self.database_url = database_url
        self.embedder = embedder  # Embedder 实例; None 时只存文本(测试用)

    async def _conn(self):
        import psycopg
        from pgvector.psycopg import register_vector_async

        conn = await psycopg.AsyncConnection.connect(self.database_url, autocommit=True)
        await register_vector_async(conn)
        return conn

    async def init_schema(self) -> None:
        conn = await self._conn()
        try:
            # 扩展按库启用, 幂等; 无权限时失败并让错误自然暴露
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(DDL)
        finally:
            await conn.close()

    async def upsert_chunks(
        self, repo: str, chunks: list[Chunk], commit_sha: str | None = None
    ) -> dict:
        """批量 upsert; 内容未变化的 chunk 不重算 embedding。"""
        conn = await self._conn()
        try:
            by_id: dict[str, Chunk] = {}
            for chunk in chunks:  # 同批内后出现者覆盖先出现者
                by_id[chunk_id(repo, chunk)] = chunk

            cur = await conn.execute(
                "SELECT id, content_hash FROM chunks WHERE id = ANY(%s)", (list(by_id),)
            )
            rows = await cur.fetchall()
            existing = {row[0]: row[1] for row in rows}

            changed: list[tuple[str, Chunk, str]] = []
            for cid, chunk in by_id.items():
                h = content_hash(chunk.text)
                if existing.get(cid) == h:
                    continue  # 内容未变, 跳过 embedding
                changed.append((cid, chunk, h))

            stats = {
                "inserted": 0,
                "updated": 0,
                "unchanged": len(chunks) - len(changed),
            }
            if not changed:
                return stats
            if self.embedder is not None:
                logger.info("批量嵌入 %d 个 chunk...", len(changed))
                vectors = self.embedder.embed_documents([c.text for _, c, _ in changed])
            else:
                vectors = [None] * len(changed)

            for (cid, chunk, h), vector in zip(changed, vectors):
                # 显式包装为 pgvector 类型: 裸 list 在部分 pgvector 版本下
                # 会被 psycopg 按 double precision[] 序列化, 导致 <=> 算子无法匹配
                await conn.execute(
                    """
                    INSERT INTO chunks
                        (id, repo, source, path, content, meta, content_hash, commit_sha, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        content = EXCLUDED.content,
                        meta = EXCLUDED.meta,
                        content_hash = EXCLUDED.content_hash,
                        commit_sha = EXCLUDED.commit_sha,
                        embedding = EXCLUDED.embedding
                    """,
                    (
                        cid,
                        repo,
                        chunk.source,
                        str(chunk.meta.get("path", "")),
                        chunk.text,
                        json.dumps(chunk.meta, ensure_ascii=False),
                        h,
                        commit_sha,
                        Vector(vector) if vector is not None else None,
                    ),
                )
                stats["updated" if cid in existing else "inserted"] += 1
            return stats
        finally:
            await conn.close()

    async def delete_by_path(self, repo: str, path: str) -> int:
        """增量更新: 删除某路径的全部 chunk, 随后重新 upsert。"""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "DELETE FROM chunks WHERE repo = %s AND path = %s", (repo, path)
            )
            return cur.rowcount
        finally:
            await conn.close()

    async def count(self, repo: str | None = None) -> int:
        conn = await self._conn()
        try:
            if repo:
                cur = await conn.execute("SELECT count(*) FROM chunks WHERE repo = %s", (repo,))
            else:
                cur = await conn.execute("SELECT count(*) FROM chunks")
            return (await cur.fetchone())[0]
        finally:
            await conn.close()
