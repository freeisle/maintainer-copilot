"""Reranker: bge-reranker-v2-m3 本地 CrossEncoder 重排。

多语种(中英), 首次运行自动下载(约 1.1GB, 走 HF_ENDPOINT 镜像)。
CPU 上对 50 个候选重排耗时数秒, 演示可接受。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self, model: str = "BAAI/bge-reranker-v2-m3") -> None:
        self.model = model
        self._encoder = None

    def _load(self):
        if self._encoder is None:
            from sentence_transformers import CrossEncoder  # 延迟导入

            logger.info("加载 reranker 模型 %s (首次运行自动下载)...", self.model)
            self._encoder = CrossEncoder(self.model)
        return self._encoder

    def rerank(self, query: str, candidates: list[dict], top_k: int | None = None) -> list[dict]:
        """candidates: [{"id","text",...}]; 按相关性降序返回, 附带 rerank_score。"""
        if not candidates:
            return []
        model = self._load()
        pairs = [(query, c["text"]) for c in candidates]
        scores = model.predict(pairs)
        ranked = sorted(zip(candidates, scores), key=lambda item: item[1], reverse=True)
        out = [{**c, "rerank_score": float(score)} for c, score in ranked]
        return out[:top_k] if top_k else out
