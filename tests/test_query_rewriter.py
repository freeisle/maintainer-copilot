"""查询改写单测：报错串抽取。"""
from maintainer_copilot.rag.query_rewriter import extract_error_strings


def test_extract_error_strings() -> None:
    errs = extract_error_strings(
        "启动时抛 java.lang.NullPointerException at UserService.getById(UserService.java:42)"
    )
    assert any("NullPointerException" in e for e in errs)


def test_extract_error_strings_empty_for_plain_question() -> None:
    assert extract_error_strings("怎么配置数据源?") == []
