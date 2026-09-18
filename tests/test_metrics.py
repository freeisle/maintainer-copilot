"""评测指标单测。"""
from eval.metrics import citation_precision, macro_f1, retrieval_hit_at_k


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


def test_citation_precision() -> None:
    cited = ["数据源配置在 application.yml", "完全无关的另一段文字"]
    answer = "你需要在 application.yml 中配置数据源"
    assert citation_precision(cited, answer) == 0.5
    assert citation_precision([], answer) == 0.0
