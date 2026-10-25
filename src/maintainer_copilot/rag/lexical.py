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
        tokens = [tokenize(t, enhanced) or ["__empty__"] for t in texts]
        self.terms = [set(t) for t in tokens]
        self.index = BM25Okapi(tokens) if texts else None

    def search(self, query: str, top_k: int) -> list[dict]:
        if self.index is None:
            return []
        query_tokens = tokenize(query, self.enhanced)
        scores = self.index.get_scores(query_tokens)
        if self.enhanced:
            # BM25 IDF can be zero/negative in tiny corpora or ubiquitous queries.
            # Retain actual token matches without admitting unrelated documents.
            query_terms = set(query_tokens)
            scores = [float(s) if s > 0 else 1e-6 * len(terms & query_terms)
                      for s,terms in zip(scores,self.terms)]
        ranked = sorted(zip(self.rows, scores), key=lambda x: x[1], reverse=True)
        return [{**r, "rerank_score": float(s)} for r, s in ranked if s > 0][:top_k]


def query_symbols(query: str) -> list[str]:
    """Extract explicit code identifiers, not natural-language words or query IDs."""
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*", query)
    return list(dict.fromkeys(w for w in words if
        "_" in w or "." in w or re.search(r"[a-z][A-Z]|[A-Z]{2}[a-z]", w)))


def symbol_search(rows: list[dict], query: str, k: int) -> list[dict]:
    """Literal, token-boundary symbol matches. No repository or language preference."""
    symbols = query_symbols(query)
    patterns = [(s, re.compile(r"(?<![A-Za-z0-9_])" + re.escape(s) + r"(?![A-Za-z0-9_])", re.I))
                for s in symbols]
    found = []
    for row in rows:
        text = row.get("path", "") + " " + row["text"]
        matches = [s for s, pattern in patterns if pattern.search(text)]
        if matches:
            found.append({**row, "symbol_matches": matches})
    return sorted(found, key=lambda d: (-len(d["symbol_matches"]), d["id"]))[:k]


def blend_document_ranks(before: list[dict], after: list[dict], k: int = 10) -> list[dict]:
    """Equal-weight rank evidence from fusion and CE; document-level, stable ties.

    Raw model scores stay unchanged. Explicit ranking_score is consumed by Solver.
    No absolute score thresholds, source-type boosts or query-specific rules.
    """
    def key(d):
        return (d.get("repo"), d.get("source"),
                d.get("meta", {}).get("issue") if d.get("source") == "issue" else d.get("path"))
    def ranks(rows):
        result = {}
        for d in rows:
            result.setdefault(key(d), len(result) + 1)
        return result
    first, second = ranks(before), ranks(after)
    scored = [{**d, "ranking_score": 1 / (k + first[key(d)]) + 1 / (k + second[key(d)])}
              for d in after]
    return sorted(scored, key=lambda d: d["ranking_score"], reverse=True)
