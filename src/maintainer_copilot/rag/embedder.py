"""Embedder 抽象：本地 bge-m3 默认, OpenAI 兼容 API 兜底。

本地模型首次运行自动下载(约 2.3GB); 无 GPU / 磁盘紧张时切换 API 模式。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self, provider: str = "local", model: str = "BAAI/bge-m3") -> None:
        self.provider = provider
        self.model = model
        self._local = None

    def _load_local(self):
        if self._local is None:
            from sentence_transformers import SentenceTransformer  # 延迟导入: rag extra 才安装

            logger.info("加载本地 embedding 模型 %s (首次运行自动下载)...", self.model)
            self._local = SentenceTransformer(self.model)
        return self._local

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "local":
            vectors = self._load_local().encode(texts, normalize_embeddings=True)
            return [v.tolist() for v in vectors]
        # TODO(D3): OpenAI 兼容 embeddings API 模式
        raise NotImplementedError("API 模式在 D3 补齐")

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
