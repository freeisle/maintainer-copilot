"""入库 CLI：三源异构数据(代码/文档/issue) -> chunk -> embed -> pgvector。

用法:
  uv run python scripts/ingest.py --repo freeisle/ragent --kind docs --file README.md
  uv run python scripts/ingest.py --repo freeisle/ragent --kind issues --file data/raw/ragent-issues.jsonl

TODO(D3/D4): 实现完整管线; 脚手架阶段验证配置与切分器。
"""
import argparse
import json
from pathlib import Path

from maintainer_copilot.rag.chunkers import DocChunker, IssueChunker


def main() -> None:
    parser = argparse.ArgumentParser(description="三源异构入库")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--kind", choices=["docs", "code", "issues"], required=True)
    parser.add_argument("--file", help="issues JSONL 或本地文档路径")
    args = parser.parse_args()

    if args.kind == "issues":
        if not args.file:
            parser.error("--kind issues 需要 --file")
        raw = Path(args.file).read_text(encoding="utf-8")
        issues = [json.loads(line) for line in raw.splitlines() if line.strip()]
        chunks = [c for issue in issues for c in IssueChunker().split(issue)]
        print(f"[STUB] {args.repo} issues -> {len(chunks)} chunks (入库在 D3 实现)")
    elif args.kind == "docs":
        if not args.file:
            parser.error("--kind docs 需要 --file")
        text = Path(args.file).read_text(encoding="utf-8")
        chunks = DocChunker().split(text, args.file)
        print(f"[STUB] {args.file} -> {len(chunks)} chunks (入库在 D3 实现)")
    else:
        print(f"[STUB] code 入库在 D4 实现(tree-sitter AST 切分)")


if __name__ == "__main__":
    main()
