"""切分器单测：标题切分 / issue 结构化切分 / 逻辑主键稳定性。"""
from maintainer_copilot.rag.chunkers import DocChunker, IssueChunker
from maintainer_copilot.rag.indexer import chunk_id, content_hash

MD = """# 安装
这里是安装说明。
## 快速开始
step1 step2
# 配置
配置说明。"""


def test_doc_chunker_splits_by_heading() -> None:
    chunks = DocChunker().split(MD, "README.md")
    headings = [c.meta.get("heading") for c in chunks]
    assert headings == ["安装", "快速开始", "配置"]
    assert all(c.meta["path"] == "README.md" for c in chunks)


def test_doc_chunker_no_heading_single_chunk() -> None:
    chunks = DocChunker().split("没有标题的纯文本", "note.txt")
    assert len(chunks) == 1
    assert chunks[0].meta == {"path": "note.txt", "part_idx": 0}


def test_doc_chunker_hard_split_long_section() -> None:
    text = "# 长章节\n" + "字" * 2500
    chunks = DocChunker(max_chars=1000).split(text, "x.md")
    assert len(chunks) == 3  # 2500 字按 1000 硬切
    assert all(c.meta["heading"] == "长章节" for c in chunks)


def test_issue_chunker_title_and_body_with_labels() -> None:
    issue = {
        "number": 5,
        "title": "启动报错",
        "body": "环境变量配置后仍然失败",
        "state": "closed",
        "labels": [{"name": "bug"}, {"name": "needs-triage"}],
    }
    chunks = IssueChunker().split(issue)
    assert len(chunks) == 2
    title_chunk = next(c for c in chunks if c.meta["part"] == "title")
    assert title_chunk.text == "启动报错"
    assert title_chunk.meta["labels"] == ["bug", "needs-triage"]
    assert title_chunk.meta["issue"] == 5


def test_issue_chunker_skips_empty_fields() -> None:
    chunks = IssueChunker().split({"number": 9, "title": "", "body": None})
    assert chunks == []


def test_chunk_id_stable_and_sensitive() -> None:
    from maintainer_copilot.rag.chunkers import Chunk

    a = Chunk(text="内容A", source="doc", meta={"path": "x.md", "heading": "h", "part_idx": 0})
    b = Chunk(text="内容B", source="doc", meta={"path": "x.md", "heading": "h", "part_idx": 0})
    assert chunk_id("repo", a) == chunk_id("repo", b)  # 逻辑位置相同 -> 同 id
    c = Chunk(text="内容A", source="doc", meta={"path": "x.md", "heading": "h2", "part_idx": 0})
    assert chunk_id("repo", a) != chunk_id("repo", c)  # 位置不同 -> 不同 id
    assert content_hash("abc") != content_hash("abd")


def test_issue_chunks_have_distinct_ids() -> None:
    """回归测试: issue chunk 的 path/heading 为空, 曾全部撞同一逻辑主键(110 条只存 1 条)。"""
    issue_a = {
        "number": 1,
        "title": "启动报错",
        "body": "复现步骤",
        "state": "open",
        "labels": [],
    }
    issue_b = {"number": 2, "title": "启动报错", "body": "复现步骤", "state": "open", "labels": []}
    chunks_a = IssueChunker().split(issue_a)
    chunks_b = IssueChunker().split(issue_b)
    ids_a = {chunk_id("r", c) for c in chunks_a}
    ids_b = {chunk_id("r", c) for c in chunks_b}
    assert len(ids_a) == len(chunks_a)  # 同 issue 的 title/body 也不互撞
    assert not ids_a & ids_b  # 不同 issue 绝不撞


def test_hard_split_chunks_have_distinct_ids() -> None:
    """回归测试: 同标题硬切出的多个分段必须有不同逻辑主键。"""
    text = "# 长章节\n" + "字" * 2500
    chunks = DocChunker(max_chars=1000).split(text, "x.md")
    ids = {chunk_id("r", c) for c in chunks}
    assert len(ids) == len(chunks)
