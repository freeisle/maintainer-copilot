"""update_kb 脚本单测: 路径过滤 / 状态文件键。"""
from scripts.update_kb import _slug, _split_file


def test_slug_replaces_slash() -> None:
    assert _slug("freeisle/ragent") == "freeisle__ragent"


def test_split_file_code_and_doc() -> None:
    code = _split_file("src/a.py", "def foo():\n    return 1\n")
    assert code and all(c.source == "code" for c in code)
    doc = _split_file("docs/b.md", "# 标题\n\n正文内容")
    assert doc and all(c.source == "doc" for c in doc)


def test_split_file_unknown_suffix_skipped() -> None:
    assert _split_file("data.bin", "anything") == []
