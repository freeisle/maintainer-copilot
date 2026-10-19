"""Shared lexical retrieval for PostgreSQL and offline snapshots."""
import re

from rank_bm25 import BM25Okapi

from .dedup import deduplicate_docs


def tokenize(text: str, enhanced: bool = True) -> list[str]:
    original = re.findall(r"[a-zA-Z0-9_]+|[一-鿿]", text.lower())
    if not enhanced:
        return original
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    split = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", split).replace("_", " ")
    # Retain full identifiers as well as their components.
    components = re.findall(r"[a-zA-Z0-9]+", split.lower())
    return original + [t for t in components if t not in original]


def diversify(rows: list[dict], top_k: int) -> list[dict]:
    """One best chunk per document first; remaining slots retain useful siblings."""
    primary, siblings, seen = [], [], set()
    for row in deduplicate_docs(rows):
        key = (row.get("repo", ""), row["source"],
               row.get("meta", {}).get("issue") if row["source"] == "issue" else row["path"])
        (siblings if key in seen else primary).append(row)
        seen.add(key)
    return (primary + siblings)[:top_k]


class LexicalIndex:
    def __init__(self, rows: list[dict], enhanced: bool = True):
        self.rows = deduplicate_docs(rows) if enhanced else rows
        self.enhanced = enhanced
        texts = [(r.get("path", "") + " " if enhanced else "") + r["text"] for r in self.rows]
        self.index = BM25Okapi([tokenize(t, enhanced) or ["__empty__"] for t in texts]) if texts else None

    def search(self, query: str, top_k: int) -> list[dict]:
        if self.index is None:
            return []
        scores = self.index.get_scores(tokenize(query, self.enhanced))
        ranked = sorted(zip(self.rows, scores), key=lambda x: x[1], reverse=True)
        return [{**r, "rerank_score": float(s)} for r, s in ranked if s > 0][:top_k]
