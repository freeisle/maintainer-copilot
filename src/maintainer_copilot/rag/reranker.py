"""Reranker: bge-reranker-v2-m3 本地 CrossEncoder 重排。

多语种(中英), 首次运行自动下载(约 1.1GB, 走 HF_ENDPOINT 镜像)。
CPU 耗时取决于候选长度与硬件；实际延迟应以独立性能报告为准。
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self, model: str = "BAAI/bge-reranker-v2-m3", batch_size: int = 32, *, revision: str | None = None) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.model = model
        self.revision = revision
        self.batch_size = batch_size
        self._encoder = None

    def _load(self):
        if self._encoder is None:
            from sentence_transformers import CrossEncoder  # 延迟导入

            logger.info("加载 reranker 模型 %s (首次运行自动下载)...", self.model)
            self._encoder = CrossEncoder(self.model, **({"revision": self.revision} if self.revision else {}))
        return self._encoder

    def rerank(self, query: str, candidates: list[dict], top_k: int | None = None) -> list[dict]:
        """candidates: [{"id","text",...}]; 按相关性降序返回, 附带 rerank_score。"""
        if not candidates:
            return []
        model = self._load()
        pairs = [(query, c["text"]) for c in candidates]
        scores = model.predict(pairs, batch_size=self.batch_size)
        if len(scores) != len(candidates) or not all(math.isfinite(float(s)) for s in scores):
            raise ValueError("Reranker returned incomplete or non-finite scores")
        ranked = sorted(zip(candidates, scores), key=lambda item: item[1], reverse=True)
        out = [{**c, "rerank_score": float(score)} for c, score in ranked]
        return out[:top_k] if top_k else out
