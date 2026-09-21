"""知识库增量更新: 对比上次索引的 commit SHA, 只处理变更文件。

流程: 状态文件(data/kb_state/{repo}.txt 记录上次 SHA)
    -> git fetch 拉最新 -> git diff --name-only 变更文件
    -> 变更文件按类型重新切分 -> delete_by_path + 批量 hash 去重 upsert
    -> 回写最新 SHA

首次使用(无状态文件)时提示先跑全量 ingest 或 --init-state 用当前 SHA 初始化。

用法:
  uv run python scripts/update_kb.py --repo freeisle/ragent --dir data/repos/ragent --branch dev_01
  uv run python scripts/update_kb.py --repo freeisle/ragent --dir data/repos/ragent --init-state
"""
import argparse
import asyncio
import logging
import subprocess
from pathlib import Path

from maintainer_copilot.config import get_settings
from maintainer_copilot.rag.chunkers import CodeChunker, DocChunker
from maintainer_copilot.rag.embedder import Embedder
from maintainer_copilot.rag.indexer import Indexer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CODE_SUFFIXES = {".java", ".py", ".ts", ".tsx", ".js"}


def _slug(repo: str) -> str:
    return repo.replace("/", "__")


def _state_path(repo: str) -> Path:
    p = get_settings().data_dir / "kb_state"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{_slug(repo)}.txt"


def _git(repo_dir: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _split_file(path: str, text: str) -> list:
    if Path(path).suffix.lower() in CODE_SUFFIXES:
        return CodeChunker().split(text, path)
    if Path(path).suffix.lower() in DOC_SUFFIXES:
        return DocChunker().split(text, path)
    return []


async def run(repo_key: str, repo_dir: Path, branch: str, init_state: bool) -> dict:
    settings = get_settings()
    indexer = Indexer(settings.database_url, embedder=Embedder())
    await indexer.init_schema()
    state = _state_path(repo_key)

    if init_state or not state.exists():
        sha = _git(repo_dir, "rev-parse", f"origin/{branch}")
        state.write_text(sha, encoding="utf-8")
        logger.info("状态初始化: last_sha=%s -> %s", sha, state)
        return {"initialized": True, "sha": sha}

    last_sha = state.read_text(encoding="utf-8").strip()
    try:
        _git(repo_dir, "fetch", "origin")
    except subprocess.CalledProcessError as exc:
        logger.warning("git fetch 失败, 使用本地已有引用继续: %s", exc.stderr.strip()[:120])
    latest_sha = _git(repo_dir, "rev-parse", f"origin/{branch}")
    if last_sha == latest_sha:
        logger.info("无新提交: %s 已是最新", last_sha)
        return {"changed": []}

    changed = _git(repo_dir, "diff", "--name-only", last_sha, latest_sha).splitlines()
    changed = [p for p in changed if p.strip()]
    logger.info("变更文件 %d 个: %s", len(changed), changed[:10])
    all_chunks: list = []
    for path in changed:
        await indexer.delete_by_path(repo_key, path)  # 删除文件也覆盖(直接删旧 chunk)
        full = repo_dir / path
        if not full.is_file():  # 被删除的文件: 只清理旧 chunk, 无需重切
            continue
        text = full.read_text(encoding="utf-8", errors="replace")
        chunks = _split_file(path, text)
        if chunks:
            logger.info("  %s: 重切 %d 个 chunk", path, len(chunks))
            all_chunks.extend(chunks)
    stats = await indexer.upsert_chunks(repo_key, all_chunks, commit_sha=latest_sha)
    state.write_text(latest_sha, encoding="utf-8")
    logger.info(
        "增量更新完成: %s..%s, %d 文件, 入库 %d 个 chunk %s; 当前总 chunk=%d",
        last_sha[:8], latest_sha[:8], len(changed), len(all_chunks), stats,
        await indexer.count(repo_key),
    )
    return {"changed": changed, "stats": stats, "last_sha": last_sha, "latest_sha": latest_sha}


def main() -> None:
    parser = argparse.ArgumentParser(description="知识库增量更新(commit SHA diffing)")
    parser.add_argument("--repo", required=True, help="逻辑知识库键, 如 freeisle/ragent")
    parser.add_argument("--dir", required=True, help="本地 git 仓库根目录")
    parser.add_argument("--branch", default="main", help="跟踪分支(默认 main)")
    parser.add_argument("--init-state", action="store_true", help="仅用当前 SHA 初始化状态, 不更新")
    args = parser.parse_args()
    asyncio.run(run(args.repo, Path(args.dir), args.branch, args.init_state))


if __name__ == "__main__":
    main()
