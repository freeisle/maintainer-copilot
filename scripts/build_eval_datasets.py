"""构建评测集: 带标签 issue 按 3:1 切分(索引集/评测真值集)。

类别映射: type/bug->bug, type/enhancement|type/proposal->feature,
type/question|type/discussion->question, type/duplicate|type/wontfix->invalid;
其余标签不参与类别判定。

评测集条目带 "repo" 字段(检索知识库键), 支持多数据源拼合:
  主源(dubbo)固定使用 eval/dubbo 键; --extra 形如 path=repo键, 可多次传入。

用法:
  uv run python scripts/build_eval_datasets.py --in data/raw/apache__dubbo-labeled.jsonl
  uv run python scripts/build_eval_datasets.py --in data/raw/apache__dubbo-labeled.jsonl \
      --extra data/raw/apache__rocketmq-labeled.jsonl=eval/rocketmq
"""
import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY_BY_LABEL = {
    "type/bug": "bug",
    "type/enhancement": "feature",
    "type/proposal": "feature",
    "type/question": "question",
    "type/discussion": "question",
    "type/duplicate": "invalid",
    "type/wontfix": "invalid",
}


def to_category(labels: list[dict]) -> str | None:
    for label in labels:
        cat = CATEGORY_BY_LABEL.get(label.get("name", ""))
        if cat:
            return cat
    return None


def split_source(src: Path, repo_key: str) -> tuple[list[dict], list[dict]]:
    """单数据源 1:4 间隔切分(与历史 dubbo 切分保持一致), 返回(评测集, 索引集)。"""
    issues = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    labeled = [(i, to_category(i.get("labels", []))) for i in issues]
    labeled = [(i, c) for i, c in labeled if c]
    logger.info("源 %s: 可用带类别标签 issue %d / %d", src, len(labeled), len(issues))
    eval_items, index_items = [], []
    for idx, (issue, cat) in enumerate(labeled):
        if idx % 4 == 0:
            eval_items.append(
                {
                    "number": issue["number"],
                    "title": issue["title"],
                    "body": issue.get("body") or "",
                    "true_category": cat,
                    "repo": repo_key,
                }
            )
        else:
            index_items.append(issue)
    index_out = src.with_name(src.name.replace("-labeled", "-index"))
    with open(index_out, "w", encoding="utf-8") as f:
        for item in index_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    logger.info("源 %s: 评测 %d, 索引 %d -> %s", src, len(eval_items), len(index_items), index_out)
    return eval_items, index_items


def main() -> None:
    parser = argparse.ArgumentParser(description="构建分诊评测集")
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--extra", action="append", default=[], help="追加数据源 path=repo键(可多次)")
    args = parser.parse_args()
    sources = [(Path(args.src), "eval/dubbo")]
    for extra in args.extra:
        path, _, repo_key = extra.partition("=")
        sources.append((Path(path), repo_key or "eval/extra"))
    eval_all: list[dict] = []
    for src, repo_key in sources:
        eval_items, _ = split_source(src, repo_key)
        eval_all.extend(eval_items)

    datasets = Path("eval/datasets")
    datasets.mkdir(parents=True, exist_ok=True)
    triage_out = datasets / "triage.jsonl"
    with open(triage_out, "w", encoding="utf-8") as f:
        for item in eval_all:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    logger.info("评测集共 %d 条 -> %s", len(eval_all), triage_out)


if __name__ == "__main__":
    main()
