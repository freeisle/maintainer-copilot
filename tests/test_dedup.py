from scripts.ingest import collect_docs, collect_code
from maintainer_copilot.rag.dedup import duplicate_aliases, deduplicate_docs


def test_subtree_and_full_scan_keep_same_identity(tmp_path):
    (tmp_path / ".git").mkdir()
    docs = tmp_path / "docs" / "releases"
    docs.mkdir(parents=True)
    (docs / "v1.md").write_text("# Release\nDetails", encoding="utf-8")
    (docs / "a.py").write_text("def a(): pass", encoding="utf-8")
    assert collect_docs(tmp_path) == collect_docs(tmp_path / "docs")
    assert collect_code(tmp_path) == collect_code(docs)
    assert collect_docs(docs)[0][0] == "docs/releases/v1.md"


def test_only_proven_aliases_are_removed():
    rows = [dict(id="a", repo="x/y", source="doc", path="releases/v.md", text="same"),
            dict(id="b", repo="x/y", source="doc", path="docs/releases/v.md", text="same"),
            dict(id="c", repo="x/y", source="doc", path="other.md", text="same"),
            dict(id="d", repo="other/repo", source="doc", path="releases/v.md", text="same"),
            dict(id="e", repo="x/y", source="doc", path="releases/v.md", text="changed")]
    assert duplicate_aliases(rows) == {"a": "b"}
    assert [r["id"] for r in deduplicate_docs(rows)] == ["b", "c", "d", "e"]
