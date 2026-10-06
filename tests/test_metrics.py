"""评测指标单测。"""
from eval.metrics import (
    citation_precision,
    doc_key,
    macro_f1,
    mrr_at_k,
    recall_at_k,
    retrieval_hit_at_k,
)


def test_macro_f1_perfect() -> None:
    report = macro_f1(["a", "b"], ["a", "b"])
    assert report["macro_f1"] == 1.0


def test_macro_f1_mixed() -> None:
    report = macro_f1(["a", "a", "b"], ["a", "b", "b"])
    per = report["per_class"]
    assert per["a"]["precision"] == 1.0
    assert per["a"]["recall"] == 0.5
    assert abs(report["macro_f1"] - 2 / 3) < 1e-9


def test_retrieval_hit_at_k() -> None:
    assert retrieval_hit_at_k({"d1"}, ["d1", "d2", "d3"], k=3) == 1.0
    assert retrieval_hit_at_k({"d9"}, ["d1", "d2", "d3"], k=3) == 0.0


def test_mrr_at_k() -> None:
    assert mrr_at_k({"a"}, ["x", "a", "y"], k=3) == 0.5
    assert mrr_at_k({"a"}, ["a", "y"], k=3) == 1.0
    assert mrr_at_k({"a"}, ["x", "y"][:1], k=1) == 0.0


def test_recall_at_k() -> None:
    assert recall_at_k({"a", "b"}, ["a", "x", "b"], k=3) == 1.0
    assert recall_at_k({"a", "b"}, ["a", "x", "y"], k=3) == 0.5
    assert recall_at_k(set(), ["a"], k=3) == 0.0


def test_doc_key() -> None:
    assert doc_key({"source": "code", "path": "a/b.java"}) == "a/b.java"
    assert doc_key({"source": "doc", "path": "README.md", "meta": {}}) == "README.md"
    assert doc_key({"source": "issue", "path": "", "meta": {"issue": 42}}) == "issue#42"
    assert doc_key({"source": "issue", "path": "x", "meta": {}}) == "x"


def test_citation_precision() -> None:
    cited = ["数据源配置在 application.yml", "完全无关的另一段文字"]
    answer = "你需要在 application.yml 中配置数据源"
    assert citation_precision(cited, answer) == 0.5
    assert citation_precision([], answer) == 0.0
