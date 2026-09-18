"""入库管线：三源 chunk -> embed -> pgvector upsert, 按 commit SHA 版本化。

设计要点:
- 内容 hash 去重: 未变化的 chunk 不重算 embedding
- 增量更新: 变更文件 re-chunk 后按 (repo, path) 删除重插
- TODO(D3): psycopg 连接池 + pgvector 建表/upsert + 批量 embed
"""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Indexer:
    """TODO(D3): 实现入库与增量更新。"""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def upsert_chunks(self, repo: str, source: str, chunks: list) -> int:
        raise NotImplementedError("Sprint 1 D3 实现")

    async def delete_by_path(self, repo: str, path: str) -> int:
        raise NotImplementedError("Sprint 1 D3 实现")
