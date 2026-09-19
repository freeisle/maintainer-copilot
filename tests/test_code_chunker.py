"""CodeChunker 单测：tree-sitter AST 切分(Java/Python)与回退。"""
from maintainer_copilot.rag.chunkers import CodeChunker
from maintainer_copilot.rag.indexer import chunk_id

JAVA = """package com.example;

import java.util.List;

/**
 * 用户服务
 */
public class UserService {

    private final UserMapper mapper;

    public List<User> listUsers() {
        return mapper.selectAll();
    }

    public User getById(Long id) {
        if (id == null) {
            throw new IllegalArgumentException("id");
        }
        return mapper.selectById(id);
    }
}
"""

PYTHON = """class Calculator:
    '''计算器'''

    def add(self, a: int, b: int) -> int:
        return a + b

    def sub(self, a: int, b: int) -> int:
        return a - b
"""


def test_java_chunker_type_and_methods() -> None:
    chunks = CodeChunker().split(JAVA, "UserService.java")
    types = [c for c in chunks if c.meta["kind"] == "type"]
    methods = [c for c in chunks if c.meta["kind"] == "method"]
    assert len(types) == 1
    assert types[0].meta["class"] == "UserService"
    assert "listUsers" in types[0].text  # 骨架含方法签名
    assert "mapper.selectAll" not in types[0].text  # 骨架不含方法体
    assert {m.meta["method"] for m in methods} == {"listUsers", "getById"}
    assert all(m.meta["class"] == "UserService" for m in methods)
    assert "UserService.listUsers" in {m.meta["symbol"] for m in methods}


def test_python_chunker() -> None:
    chunks = CodeChunker().split(PYTHON, "calc.py")
    types = [c for c in chunks if c.meta["kind"] == "type"]
    methods = [c for c in chunks if c.meta["kind"] == "method"]
    assert len(types) == 1 and types[0].meta["class"] == "Calculator"
    assert {m.meta["method"] for m in methods} == {"add", "sub"}


def test_unknown_suffix_falls_back_to_window() -> None:
    chunks = CodeChunker(max_chars=10).split("x" * 25, "note.txt")
    assert len(chunks) == 3
    assert all(c.source == "code" for c in chunks)


def test_method_chunk_ids_unique() -> None:
    """回归测试: 同类的多个方法 chunk 不得撞逻辑主键。"""
    chunks = [c for c in CodeChunker().split(JAVA, "UserService.java") if c.meta["kind"] == "method"]
    ids = {chunk_id("r", c) for c in chunks}
    assert len(ids) == len(chunks)
