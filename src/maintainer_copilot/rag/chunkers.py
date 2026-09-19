"""文档切分器：三源异构切分策略。

- DocChunker: 按 Markdown 标题层级切分(标题路径作元数据), 超长段硬切
- IssueChunker: issue 标题/正文各自成 chunk, 标签与状态入 meta(弱监督信号)
- CodeChunker: tree-sitter AST 切分(D4 实现), 暂以行窗兜底跑通管线
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_H1 = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    text: str
    source: str  # doc | issue | code
    meta: dict[str, Any] = field(default_factory=dict)


class DocChunker:
    def __init__(self, max_chars: int = 1200) -> None:
        self.max_chars = max_chars

    def split(self, text: str, path: str) -> list[Chunk]:
        positions = [(m.start(), len(m.group(1)), m.group(2).strip()) for m in _H1.finditer(text)]
        chunks: list[Chunk] = []
        for i, (pos, level, title) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
            body = text[pos:end].strip()
            if not body:
                continue
            for idx, sub in enumerate(self._hard_split(body)):
                chunks.append(
                    Chunk(
                        text=sub,
                        source="doc",
                        meta={
                            "path": path,
                            "heading": title,
                            "level": level,
                            "part_idx": idx,  # 硬切分段序号: 保证逻辑主键唯一
                        },
                    )
                )
        if not positions and text.strip():
            for idx, sub in enumerate(self._hard_split(text.strip())):
                chunks.append(Chunk(text=sub, source="doc", meta={"path": path, "part_idx": idx}))
        return chunks

    def _hard_split(self, body: str) -> list[str]:
        return [body[i : i + self.max_chars] for i in range(0, len(body), self.max_chars)]


class IssueChunker:
    """issue 结构化切分: 标题/正文各自成 chunk; 标签是分诊评测的弱标注来源。"""

    def split(self, issue: dict) -> list[Chunk]:
        chunks: list[Chunk] = []
        num = issue["number"]
        base = {
            "issue": num,
            "state": issue.get("state"),
            "labels": [l["name"] for l in issue.get("labels", [])],
        }
        if issue.get("title"):
            chunks.append(Chunk(text=issue["title"], source="issue", meta={**base, "part": "title"}))
        if issue.get("body"):
            chunks.append(
                Chunk(text=issue["body"][:4000], source="issue", meta={**base, "part": "body"})
            )
        return chunks


_CODE_SUFFIXES = {
    ".java": "java",
    ".py": "python",
    ".ts": "tsx",
    ".tsx": "tsx",
    ".js": "tsx",  # tsx 语法为 ts/js 超集
}

_TYPE_NODES = {
    "java": ("class_declaration", "interface_declaration", "enum_declaration"),
    "python": ("class_definition",),
    "tsx": ("class_declaration", "interface_declaration", "enum_declaration"),
}

_METHOD_NODES = {
    "java": ("method_declaration", "constructor_declaration"),
    "python": ("function_definition",),
    "tsx": ("method_definition", "function_declaration"),
}

_FIELD_NODES = {
    "java": ("field_declaration",),
    "python": (),
    "tsx": ("public_field_definition", "property_signature"),
}

_BODY_TYPES = {
    "java": ("class_body", "interface_body", "enum_body"),
    "python": ("block",),
    "tsx": ("class_body", "interface_body", "enum_body"),
}


def _node_name(src: bytes, node: Any) -> str:
    name = node.child_by_field_name("name")
    if name is not None:
        return src[name.start_byte : name.end_byte].decode("utf-8", errors="replace")
    return "<anonymous>"


class CodeChunker:
    """tree-sitter AST 切分: 类骨架 + 方法两级切分, 携带 file:class:method 元数据。

    - 类型骨架 chunk: 声明头 + 字段 + 方法签名(不含方法体, 控制冗余)
    - 方法级 chunk: 完整方法体, symbol=Class.method, 含行号
    - 未知扩展名或解析失败回退行窗切分
    """

    def __init__(self, max_chars: int = 1500) -> None:
        self.max_chars = max_chars
        self._parsers: dict[str, Any] = {}

    def _parser(self, lang_name: str):
        if lang_name not in self._parsers:
            from tree_sitter import Language, Parser

            if lang_name == "java":
                import tree_sitter_java

                lang = Language(tree_sitter_java.language())
            elif lang_name == "python":
                import tree_sitter_python

                lang = Language(tree_sitter_python.language())
            else:  # tsx 超集, 兼容 js/ts
                import tree_sitter_typescript

                lang = Language(tree_sitter_typescript.language_tsx())
            parser = Parser()
            parser.language = lang
            self._parsers[lang_name] = parser
        return self._parsers[lang_name]

    def split(self, text: str, path: str) -> list[Chunk]:
        lang_name = _CODE_SUFFIXES.get(Path(path).suffix.lower())
        if lang_name is None:
            return self._fallback(text, path)
        src = text.encode("utf-8")
        try:
            tree = self._parser(lang_name).parse(src)
        except Exception:  # noqa: BLE001 - 解析失败回退行窗
            return self._fallback(text, path)
        chunks: list[Chunk] = []
        type_nodes = _TYPE_NODES[lang_name]
        method_nodes = _METHOD_NODES[lang_name]
        for node in tree.root_node.named_children:
            if node.type in type_nodes:
                chunks.extend(self._split_type(src, node, path, lang_name))
            elif node.type in method_nodes:
                chunks.extend(self._split_method(src, node, path, "<module>", lang_name))
        return chunks or self._fallback(text, path)

    def _body_of(self, node: Any, lang_name: str):
        """类/接口声明的方法与字段在体节点(class_body/block)内部, 需先定位。"""
        for child in node.children:
            if child.type in _BODY_TYPES[lang_name]:
                return child
        return None

    def _split_type(self, src: bytes, node: Any, path: str, lang_name: str) -> list[Chunk]:
        name = _node_name(src, node)
        base = {"path": path, "class": name}
        chunks: list[Chunk] = []
        skeleton = self._skeleton(src, node, lang_name)
        if skeleton.strip():
            chunks.append(
                Chunk(text=skeleton, source="code", meta={**base, "symbol": name, "kind": "type"})
            )
        body = self._body_of(node, lang_name)
        members = body.named_children if body is not None else []
        for child in members:
            if child.type in _METHOD_NODES[lang_name]:
                chunks.extend(self._split_method(src, child, path, name, lang_name))
        return chunks

    def _split_method(
        self, src: bytes, node: Any, path: str, class_name: str, lang_name: str
    ) -> list[Chunk]:
        m_name = _node_name(src, node)
        text = src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        meta = {
            "path": path,
            "class": class_name,
            "method": m_name,
            "symbol": f"{class_name}.{m_name}",
            "kind": "method",
            "start": node.start_point[0] + 1,
        }
        if len(text) <= self.max_chars:
            return [Chunk(text=text, source="code", meta=meta)]
        return [
            Chunk(text=text[i : i + self.max_chars], source="code", meta={**meta, "part_idx": idx})
            for idx, i in enumerate(range(0, len(text), self.max_chars))
        ]

    def _skeleton(self, src: bytes, node: Any, lang_name: str) -> str:
        """类型骨架: 声明头 + 方法签名(不含方法体) + 字段。"""
        lines: list[str] = []
        body = self._body_of(node, lang_name)
        header_end = body.start_byte if body is not None else node.end_byte
        header = src[node.start_byte : header_end].decode("utf-8", errors="replace").strip()
        if header:
            lines.append(header)
        members = body.named_children if body is not None else []
        for child in members:
            if child.type in _METHOD_NODES[lang_name]:
                method_body = child.child_by_field_name("body")
                end = method_body.start_byte if method_body is not None else child.end_byte
                sig = src[child.start_byte : end].decode("utf-8", errors="replace").strip()
                if sig:
                    lines.append(sig + " { ... }")
            elif child.type in _FIELD_NODES[lang_name]:
                lines.append(
                    src[child.start_byte : child.end_byte].decode("utf-8", errors="replace").strip()
                )
        return "\n".join(lines)

    def _fallback(self, text: str, path: str) -> list[Chunk]:
        return [
            Chunk(text=text[i : i + self.max_chars], source="code", meta={"path": path})
            for i in range(0, len(text), self.max_chars)
        ]
