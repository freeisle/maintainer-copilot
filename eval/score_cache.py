"""Evaluation-only cache of actual CrossEncoder scores; not a mock model.

Keys include model revision, runtime versions, batch size, query and full text.
Ranking uses input order for ties. Corpus/DB provenance belongs to each report.
"""
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from maintainer_copilot.rag.reranker import Reranker


class CachedReranker(Reranker):
    def __init__(self, cache_path, revision, batch_size=4):
        super().__init__(batch_size=batch_size, revision=revision)
        self.cache_path = Path(cache_path)
        self.signature = [self.model, revision, batch_size,
                          version("torch"), version("sentence-transformers")]
        self.cache = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.hits = self.misses = 0

    def key(self, query, text):
        return hashlib.sha256(json.dumps([self.signature, query, text], ensure_ascii=False).encode()).hexdigest()

    def rerank(self, query, candidates, top_k=None):
        missing = [d for d in candidates if self.key(query, d["text"]) not in self.cache]
        self.hits += len(candidates) - len(missing)
        self.misses += len(missing)
        if missing:
            for d in super().rerank(query, missing):
                self.cache[self.key(query, d["text"])] = d["rerank_score"]
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.cache), encoding="utf-8")
            temp.replace(self.cache_path)
        ranked = [{**d, "rerank_score": self.cache[self.key(query, d["text"]) ]} for d in candidates]
        ranked.sort(key=lambda d: d["rerank_score"], reverse=True)
        return ranked[:top_k] if top_k else ranked
