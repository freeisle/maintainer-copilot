"""入库 CLI：三源异构数据(代码/文档/issue) -> chunk -> embed -> pgvector。

repo 键为"逻辑知识库"而非数据来源:
  上游 issue 知识与 fork 代码/文档同属一个产品, 用同一个 repo 键索引。

用法:
  uv run python scripts/ingest.py --repo freeisle/ragent --kind docs --dir data/repos/ragent
  uv run python scripts/ingest.py --repo freeisle/ragent --kind issues --file data/raw/nageoffer__ragent-issues.jsonl
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path

from maintainer_copilot.config import get_settings
from maintainer_copilot.rag.chunkers import CodeChunker, DocChunker, IssueChunker
from maintainer_copilot.rag.embedder import Embedder
from maintainer_copilot.rag.indexer import Indexer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CODE_SUFFIXES = {".java", ".py", ".ts", ".tsx", ".js"}


def collect_docs(root: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in DOC_SUFFIXES and ".git" not in p.parts:
            rel = str(p.relative_to(root)).replace("\\", "/")
            out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
    return out


def collect_code(root: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in CODE_SUFFIXES and ".git" not in p.parts:
            rel = str(p.relative_to(root)).replace("\\", "/")
            out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
    return out


async def ingest_docs(
    indexer: Indexer, repo: str, doc_dir: str, commit_sha: str | None = None
) -> tuple[int, dict]:
    """先切分全部文档, 再一次性批量嵌入 upsert。"""
    chunker = DocChunker()
    docs = collect_docs(Path(doc_dir))
    logger.info("%s: 发现 %d 个文档文件, 切分中...", repo, len(docs))
    all_chunks: list = []
    for rel, text in docs:
        all_chunks.extend(chunker.split(text, rel))
    logger.info("%s: 切分出 %d 个 chunk, 批量嵌入入库...", repo, len(all_chunks))
    stats = await indexer.upsert_chunks(repo, all_chunks, commit_sha)
    return len(all_chunks), stats


async def ingest_issues(
    indexer: Indexer, repo: str, issues_file: str, commit_sha: str | None = None
) -> tuple[int, dict]:
    raw = Path(issues_file).read_text(encoding="utf-8")
    issues = [json.loads(line) for line in raw.splitlines() if line.strip()]
    chunker = IssueChunker()
    chunks = [c for issue in issues for c in chunker.split(issue)]
    stats = await indexer.upsert_chunks(repo, chunks, commit_sha)
    return len(chunks), stats


async def ingest_code(
    indexer: Indexer, repo: str, code_dir: str, commit_sha: str | None = None
) -> tuple[int, dict]:
    """先切分全部文件, 再一次性批量嵌入 upsert(避免逐文件 encode 的会话开销)。"""
    chunker = CodeChunker()
    files = collect_code(Path(code_dir))
    logger.info("%s: 发现 %d 个代码文件, 切分中...", repo, len(files))
    all_chunks: list = []
    for rel, text in files:
        all_chunks.extend(chunker.split(text, rel))
    logger.info("%s: 切分出 %d 个 chunk, 批量嵌入入库...", repo, len(all_chunks))
    stats = await indexer.upsert_chunks(repo, all_chunks, commit_sha)
    return len(all_chunks), stats


def main() -> None:
    parser = argparse.ArgumentParser(description="三源异构入库")
    parser.add_argument("--repo", required=True, help="逻辑知识库键, 如 freeisle/ragent")
    parser.add_argument("--kind", choices=["docs", "code", "issues"], required=True)
    parser.add_argument("--file", help="issues JSONL 路径")
    parser.add_argument("--dir", help="本地仓库根目录(docs/code 用)")
    parser.add_argument("--commit-sha", help="版本标记(默认取仓库 HEAD)")
    args = parser.parse_args()

    settings = get_settings()
    indexer = Indexer(settings.database_url, embedder=Embedder())

    async def run() -> None:
        await indexer.init_schema()
        if args.kind == "issues":
            if not args.file:
                parser.error("--kind issues 需要 --file")
            total, stats = await ingest_issues(indexer, args.repo, args.file, args.commit_sha)
            logger.info("issues 入库完成: %d chunks %s", total, stats)
        elif args.kind == "docs":
            if not args.dir:
                parser.error("--kind docs 需要 --dir")
            total, stats = await ingest_docs(indexer, args.repo, args.dir, args.commit_sha)
            logger.info("docs 入库完成: %d chunks %s", total, stats)
        else:  # code
            if not args.dir:
                parser.error("--kind code 需要 --dir")
            total, stats = await ingest_code(indexer, args.repo, args.dir, args.commit_sha)
            logger.info("code 入库完成: %d chunks %s", total, stats)
        logger.info("当前 %s 总 chunk 数: %d", args.repo, await indexer.count(args.repo))

    asyncio.run(run())


if __name__ == "__main__":
    main()
