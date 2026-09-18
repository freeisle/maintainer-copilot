"""GitHub 数据采集：issues(+labels+comments) 落本地 JSONL。

- 幂等: 按 issue 编号去重, 可重复执行
- 双用途: 索引源(issues) + 评测集(closed+labeled)
- 用法: uv run python scripts/collect_github_data.py --repo freeisle/ragent --out data/raw/ragent-issues.jsonl

TODO(D2): 分页 + 限流降级 + 断点续采。
"""
import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="采集 GitHub issues 到本地 JSONL")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"[STUB] 采集 {args.repo} issues -> {args.out}（D2 实现: 分页/去重/断点续采）")


if __name__ == "__main__":
    main()
