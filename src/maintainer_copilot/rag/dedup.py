"""Conservative legacy docs alias detection; never conflate distinct files."""
from __future__ import annotations


def duplicate_aliases(rows: list[dict]) -> dict[str, str]:
    """Map legacy id to canonical id only for identical docs/path pairs.

    The full text, heading and part must match. A prefix alone is not proof.
    Callers scope rows to a single repository, or supply repo in each row.
    """
    def key(row, path):
        meta = row.get("meta") or {}
        return (row.get("repo", ""), path, row["text"],
                meta.get("heading", ""), meta.get("part_idx", 0))

    canonical = {key(r, r["path"]): r["id"] for r in rows
                 if r["source"] == "doc" and r["path"].startswith("docs/")}
    return {r["id"]: canonical[key(r, "docs/" + r["path"])] for r in rows
            if r["source"] == "doc" and not r["path"].startswith("docs/")
            and key(r, "docs/" + r["path"]) in canonical}


def deduplicate_docs(rows: list[dict]) -> list[dict]:
    aliases = duplicate_aliases(rows)
    return [r for r in rows if r["id"] not in aliases]
