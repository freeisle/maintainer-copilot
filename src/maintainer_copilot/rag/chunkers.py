"""文档切分器：三源异构切分策略。

- DocChunker: 按 Markdown 标题层级切分(标题路径作元数据), 超长段硬切
- IssueChunker: issue 标题/正文各自成 chunk, 标签与状态入 meta(弱监督信号)
- CodeChunker: tree-sitter AST 切分(D4 实现), 暂以行窗兜底跑通管线
"""
import re
from dataclasses import dataclass, field
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


class CodeChunker:
    """tree-sitter AST 切分: 按类/方法边界切, 携带 file:class:method 元数据。

    TODO(D4): 接入 tree-sitter-java/python/typescript; 当前以行窗切分兜底跑通管线。
    """

    def __init__(self, max_chars: int = 1500) -> None:
        self.max_chars = max_chars

    def split(self, text: str, path: str) -> list[Chunk]:
        return [
            Chunk(text=text[i : i + self.max_chars], source="code", meta={"path": path})
            for i in range(0, len(text), self.max_chars)
        ]
