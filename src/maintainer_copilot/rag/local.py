"""Read-only JSONL snapshot retrieval, independent of GitHub/LLM/PostgreSQL."""
import json
from pathlib import Path

from .lexical import LexicalIndex, diversify


class LocalRetriever:
    def __init__(self, path: str | Path, enhanced: bool = True):
        self.path = Path(path)
        self.enhanced = enhanced
        self._stamp = None
        self._indexes = {}

    def _load(self):
        stamp = (self.path.stat().st_mtime_ns, self.path.stat().st_size)
        if stamp == self._stamp:
            return
        grouped = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                for field in ("repo", "id", "path", "source", "text"):
                    if not isinstance(row.get(field), str):
                        raise ValueError(f"Invalid snapshot field: {field}")
                grouped.setdefault(row["repo"], []).append(row)
        self._indexes = {repo: LexicalIndex(rows, self.enhanced) for repo, rows in grouped.items()}
        self._stamp = stamp

    async def retrieve(self, query: str, repo: str, top_k: int = 8, **kwargs) -> list[dict]:
        self._load()
        index = self._indexes.get(repo)
        if index is None:
            return []
        rows = index.search(query, max(top_k, 50))
        return diversify(rows, top_k) if self.enhanced else rows[:top_k]
