"""一键评测：从 eval/datasets/*.jsonl 读取, 输出报告到 docs/eval-report/。

用法: uv run python -m eval.run_eval --suite qa
数据集格式(JSONL):
  qa:   {"question":..., "reference":..., "candidate":..., "cited_texts":[...]}
  triage: {"issue":..., "true_label":..., "pred_label":...}
"""
import argparse
import json
import sys
from pathlib import Path

from . import metrics

SUITES = ("qa", "triage", "tool_selection")


def load_dataset(name: str) -> list[dict]:
    path = Path(__file__).parent / "datasets" / f"{name}.jsonl"
    if not path.exists():
        print(f"[warn] 数据集不存在: {path} (D2/D9 采集后生成)", file=sys.stderr)
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="评测入口")
    parser.add_argument("--suite", choices=SUITES, required=True)
    args = parser.parse_args()
    data = load_dataset(args.suite)
    if not data:
        return
    # TODO(D9): 按套件类型接入对应评测器(judge / 分诊 F1 / 工具选择)
    if args.suite == "triage" and data and "true_label" in data[0] and "pred_label" in data[0]:
        report = metrics.macro_f1(
            [d["true_label"] for d in data], [d["pred_label"] for d in data]
        )
        print(f"triage macro-F1: {report['macro_f1']:.4f}")
        for lab, v in report["per_class"].items():
            print(f"  {lab}: f1={v['f1']:.4f} (support={v['support']})")
        return
    print(f"载入 {len(data)} 条 {args.suite} 样本, 评测器在 D9 接入")


if __name__ == "__main__":
    main()
