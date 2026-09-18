"""GitHub 数据采集：issues(+labels+comments) 落本地 JSONL。

设计要点:
- 幂等去重: 按 issue 编号去重, 可重复执行/断点续采(只追加新增, 脏行跳过)
- 限流感知: 每次请求前检查 rate_remaining, 匿名余量低时提示配置 MC_GITHUB_TOKEN
- 评论按需: 仅拉取 comments_count > 0 的 issue 评论, 省 80%+ 请求量
- 双用途: 同一份数据既是索引源(issues), 又是评测集原料(closed+labeled)
- 用法: uv run python scripts/collect_github_data.py --repo freeisle/ragent
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path

from maintainer_copilot.config import get_settings
from maintainer_copilot.tools.github_client import GitHubClient, RateLimitExceeded

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PER_PAGE = 100


def load_existing_numbers(out: Path) -> set[int]:
    if not out.exists():
        return set()
    numbers: set[int] = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            numbers.add(json.loads(line)["number"])
        except (json.JSONDecodeError, KeyError):
            continue  # 脏行跳过, 不影响续采
    return numbers


def append_issue(out: Path, issue: dict) -> None:
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(issue, ensure_ascii=False) + "\n")


async def collect_issues(
    client: GitHubClient,
    repo: str,
    out: Path,
    *,
    include_comments: bool = True,
    per_page: int = PER_PAGE,
    max_pages: int = 100,
) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing_numbers(out)
    page, collected, skipped, comments_fetched = 1, 0, 0, 0
    while True:
        if page > max_pages:
            raise RuntimeError(f"分页异常: 超过 {max_pages} 页, 疑似数据源异常, 请检查")
        if client.rate_remaining and client.rate_remaining < 5:
            raise RateLimitExceeded(
                f"限流余量不足({client.rate_remaining}/h), 请配置 MC_GITHUB_TOKEN 后重跑(幂等续采)"
            )
        issues = await client.list_issues(repo, state="all", page=page, per_page=per_page)
        if not issues:
            break
        for issue in issues:
            if "pull_request" in issue:
                continue  # issues 接口混入 PR, 跳过
            num = issue["number"]
            if num in existing:
                skipped += 1
                continue
            if include_comments and issue.get("comments", 0) > 0:
                issue["comments_data"] = await client.list_comments(repo, num)
                comments_fetched += 1
            append_issue(out, issue)
            existing.add(num)
            collected += 1
            if collected % 20 == 0:
                logger.info(
                    "%s: 已采集 %d 条 (rate_remaining=%d)",
                    repo, collected, client.rate_remaining,
                )
        if len(issues) < per_page:
            break
        page += 1
    return {
        "collected": collected,
        "skipped": skipped,
        "comments_fetched": comments_fetched,
        "total": len(existing),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="采集 GitHub issues 到本地 JSONL")
    parser.add_argument("--repo", required=True, help="仓库全名, 如 freeisle/ragent")
    parser.add_argument(
        "--out", help="输出 JSONL 路径(默认 data/raw/{repo-slug}-issues.jsonl)"
    )
    args = parser.parse_args()
    out = (
        Path(args.out)
        if args.out
        else Path("data") / "raw" / f"{args.repo.replace('/', '__')}-issues.jsonl"
    )
    client = GitHubClient(token=get_settings().github_token)
    try:
        stats = asyncio.run(collect_issues(client, args.repo, out))
    except RateLimitExceeded as exc:
        logger.error("限流中止: %s", exc)
        raise SystemExit(2) from exc
    logger.info(
        "完成 %s: 新增 %d, 跳过 %d, 总计 %d -> %s",
        args.repo, stats["collected"], stats["skipped"], stats["total"], out,
    )


if __name__ == "__main__":
    main()
