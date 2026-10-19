import json
import pytest
from maintainer_copilot.rag.lexical import tokenize, diversify
from maintainer_copilot.rag.local import LocalRetriever
from maintainer_copilot.rag.retriever import rrf_fuse


def test_identifier_components_and_duplicate_rrf():
    assert {"vector", "target", "resolver", "cache", "key"} <= set(tokenize("VectorTargetResolver _cache_key"))
    assert rrf_fuse([["a", "a"]]) == rrf_fuse([["a"]])


def test_document_coverage():
    rows = [dict(id=str(i), path=p, source="code", text=str(i)) for i, p in enumerate(["a", "a", "b", "c"])]
    assert [r["path"] for r in diversify(rows, 3)] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_snapshot_isolation_refresh_and_invalid(tmp_path):
    path = tmp_path / "index.jsonl"
    def write(repo):
        rows = [dict(repo=repo, id=str(i), path=f"{i}.py", source="code", text=t) for i, t in enumerate(["unique needle", "other value", "unrelated content"])]
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    write("a/b")
    retriever = LocalRetriever(path)
    assert (await retriever.retrieve("needle", "a/b"))[0]["id"] == "0"
    assert await retriever.retrieve("needle", "private/repo") == []
    write("c/d")
    assert await retriever.retrieve("needle", "a/b") == []
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        await retriever.retrieve("needle", "a/b")

def test_tiny_corpus_common_terms_remain_retrievable():
    from maintainer_copilot.rag.lexical import LexicalIndex
    index=LexicalIndex([{'id':'1','repo':'a/b','source':'doc','path':'a.md','text':'needle'}])
    assert index.search('needle',3)[0]['id']=='1'
    assert index.search('absent',3)==[]
