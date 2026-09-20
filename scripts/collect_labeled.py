"""按标签采集已关闭 issue(评测集原料)。

用 GitHub 搜索 API 按标签抓取, 相比全量列表采集省 90% 请求:
  每个标签一次搜索(100 条/页), 只取 title/body/labels 字段, 不拉评论。

用法:
  uv run python scripts/collect_labeled.py --repo apache/dubbo --labels "type/bug,type/enhancement,type/question" --out data/raw/apache__dubbo-labeled.jsonl
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path

from maintainer_copilot.config import get_settings
from maintainer_copilot.tools.github_client import GitHubClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KEEP_FIELDS = ("number", "title", "body", "labels", "state")


def _slim(issue: dict) -> dict:
    return {
        k: issue.get(k)
        for k in KEEP_FIELDS
    }


async def collect_labeled(client: GitHubClient, repo: str, labels: list[str], out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    existing.add(json.loads(line)["number"])
                except (json.JSONDecodeError, KeyError):
                    continue
    seen: set[int] = existing.copy()
    collected = 0
    for label in labels:
        page = 1
        while page <= 3:  # 每标签最多 300 条
            query = f"is:issue is:closed label:{label}"
            result = await client.search_issues(repo, query, per_page=100)
            items = result.get("items", [])
            if not items:
                break
            for item in items:
                num = item["number"]
                if num in seen:
                    continue
                seen.add(num)
                with open(out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(_slim(item), ensure_ascii=False) + "\n")
                collected += 1
            if len(items) < 100:
                break
            page += 1
        logger.info("label %s: 累计 %d 条", label, collected)
    return {"collected": collected, "total": len(seen)}


def main() -> None:
    parser = argparse.ArgumentParser(description="按标签采集已关闭 issue")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--labels", required=True, help="逗号分隔的标签")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    client = GitHubClient(token=get_settings().github_token)
    labels = [l.strip() for l in args.labels.split(",") if l.strip()]
    stats = asyncio.run(collect_labeled(client, args.repo, labels, Path(args.out)))
    logger.info("完成: 新增 %d, 总计 %d -> %s", stats["collected"], stats["total"], args.out)


if __name__ == "__main__":
    main()
