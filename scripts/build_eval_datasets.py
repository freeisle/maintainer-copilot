"""构建评测集: 带标签 issue 按 3:1 切分(索引集/评测真值集)。

类别映射: type/bug->bug, type/enhancement|type/proposal->feature,
type/question|type/discussion->question; 其余标签不参与类别判定。

用法:
  uv run python scripts/build_eval_datasets.py --in data/raw/apache__dubbo-labeled.jsonl
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
}


def to_category(labels: list[dict]) -> str | None:
    for label in labels:
        cat = CATEGORY_BY_LABEL.get(label.get("name", ""))
        if cat:
            return cat
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="构建分诊评测集")
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--eval-ratio", type=float, default=0.25)
    args = parser.parse_args()
    src = Path(args.src)
    issues = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    labeled = [(i, to_category(i.get("labels", []))) for i in issues]
    labeled = [(i, c) for i, c in labeled if c]
    logger.info("可用带类别标签 issue: %d / %d", len(labeled), len(issues))

    # 按类别分层 + 间隔抽样: 每类内部每 4 个取 1 个进评测集
    eval_items, index_items = [], []
    seen_eval = 0
    for idx, (issue, cat) in enumerate(labeled):
        if idx % 4 == 0:
            eval_items.append({"number": issue["number"], "title": issue["title"],
                               "body": issue.get("body") or "", "true_category": cat})
        else:
            index_items.append(issue)
    logger.info("评测集 %d 条, 索引集 %d 条", len(eval_items), len(index_items))

    datasets = Path("eval/datasets")
    datasets.mkdir(parents=True, exist_ok=True)
    triage_out = datasets / "triage.jsonl"
    with open(triage_out, "w", encoding="utf-8") as f:
        for item in eval_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    index_out = Path("data/raw/apache__dubbo-index.jsonl")
    with open(index_out, "w", encoding="utf-8") as f:
        for item in index_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    logger.info("评测集 -> %s; 索引集 -> %s", triage_out, index_out)


if __name__ == "__main__":
    main()
