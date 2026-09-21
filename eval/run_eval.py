"""一键评测：triage(分诊 macro-F1) / qa(LLM-as-judge 三维 rubric + 硬指标)。

用法:
  uv run python -m eval.run_eval --suite triage --repo eval/dubbo
  uv run python -m eval.run_eval --suite qa
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    # psycopg 异步不兼容 ProactorEventLoop; eval 入口独立于包, 需在此先行设置
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from . import metrics
from .judge import Judge

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 进度日志落盘: 长跑时管道缓冲会掩盖进度, 文件日志可随时 tail
_progress_log = Path(__file__).resolve().parent.parent / "data" / "eval-progress.log"
_progress_log.parent.mkdir(parents=True, exist_ok=True)
_fh = logging.FileHandler(_progress_log, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(_fh)

SUITES = ("qa", "triage", "tool_selection")
DATASETS = Path(__file__).parent / "datasets"
REPORT_DIR = Path(__file__).resolve().parent.parent / "docs" / "eval-report"


def load_dataset(name: str) -> list[dict]:
    path = DATASETS / f"{name}.jsonl"
    if not path.exists():
        logger.error("数据集不存在: %s", path)
        sys.exit(1)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_report(suite: str, report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = REPORT_DIR / f"{suite}-{stamp}.md"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("报告 -> %s", path)
    return path


async def run_triage_suite(repo: str, limit: int = 0) -> dict:
    """分诊评测: 与真实 label 类别比对 macro-F1。"""
    from maintainer_copilot.workers.triage import TriageWorker

    data = load_dataset("triage")
    if limit:
        data = data[:limit]
    worker = TriageWorker()
    y_true: list[str] = []
    y_pred: list[str] = []
    details: list[dict] = []
    for i, item in enumerate(data, 1):
        repo_key = item.get("repo") or repo
        r = await worker.triage(repo_key, item["title"], item["body"], item["number"])
        cat = r.get("category", "invalid")
        y_true.append(item["true_category"])
        y_pred.append(cat)
        details.append(
            {
                "number": item["number"],
                "repo": repo_key,
                "title": item["title"][:60],
                "true": item["true_category"],
                "pred": cat,
                "confidence": r.get("confidence"),
                "correct": cat == item["true_category"],
            }
        )
        logger.info(
            "[%d/%d] #%s true=%s pred=%s conf=%s",
            i, len(data), item["number"], item["true_category"], cat, r.get("confidence"),
        )
    report = metrics.macro_f1(y_true, y_pred)
    report["details"] = details
    report["samples"] = len(data)
    write_report("triage", report)
    return report


async def run_qa_suite(limit: int = 0) -> dict:
    """问答评测: LLM-as-judge 三维 rubric + 引用有效性硬指标。"""
    from maintainer_copilot.models.llm import ModelProvider
    from maintainer_copilot.workers.solver import SolverWorker, parse_citations

    data = load_dataset("qa")
    if limit:
        data = data[:limit]
    worker = SolverWorker()
    judge = Judge(ModelProvider())
    results: list[dict] = []
    for i, item in enumerate(data, 1):
        repo = item["repo"]
        question = item["question"]
        r = await worker.solve(question, repo, advice="")
        answer = r["draft"]
        citations = r["citations"]
        cited_texts = [c.get("snippet", "") for c in citations]
        # 判官引用列表必须用上下文原始编号: 回答中的 [n] 对应上下文第 n 条
        # (此前传过滤后的 citations 重新编号, 与回答编号错配, 分数被系统性压低)
        docs = r.get("docs", [])
        judge_citations = [
            {"source": d["source"], "path": d["path"], "snippet": d["text"][:150]}
            for d in docs[:8]
        ]
        verdict = await judge.score_no_ref(
            citations=judge_citations, question=question, candidate=answer
        )
        results.append(
            {
                "repo": repo,
                "question": question,
                "answer_preview": answer[:200],
                "n_citations": len(citations),
                "has_citation_markers": bool(parse_citations(answer)),
                "judge": verdict,
                "citation_validity_hard": metrics.citation_precision(cited_texts, answer),
            }
        )
        logger.info("[%d/%d] %s judge=%s", i, len(data), question[:30], verdict)
    # 汇总平均分
    def avg(key: str) -> float:
        scores = [v["judge"].get(key) for v in results if isinstance(v["judge"].get(key), (int, float))]
        return round(sum(scores) / len(scores), 2) if scores else -1.0

    report = {
        "samples": len(data),
        "avg_correctness": avg("correctness"),
        "avg_citation_support": avg("citation_support"),
        "avg_usefulness": avg("usefulness"),
        "judge_errors": sum(1 for v in results if "error" in v["judge"]),
        "results": results,
    }
    write_report("qa", report)
    return report


async def run_tool_selection_suite(ablate: bool = False) -> dict:
    """工具选择评测: 查询 -> 期望工具 的准确率 + 混淆分布(验证工具描述质量)。

    ablate=True: 用模糊描述(复述工具名)替换"何时用/何时不用"描述做消融对照,
    量化描述质量对选择准确率的贡献。
    """
    from dataclasses import replace

    from maintainer_copilot.tools.github_tools import build_github_tools
    from maintainer_copilot.tools.registry import ToolRegistry
    from maintainer_copilot.tools.selector import ToolSelector

    registry = build_github_tools()
    if ablate:
        vague = {
            "get_issue": "获取 issue 数据",
            "search_issues": "搜索 issue",
            "read_file": "读取文件",
        }
        bare = ToolRegistry()
        for name in registry.tool_names():
            bare.register(replace(registry.get(name), description=vague[name]))
        registry = bare
    selector = ToolSelector(registry=registry)
    data = load_dataset("tool_selection")
    y_true: list[str] = []
    y_pred: list[str] = []
    details: list[dict] = []
    for i, item in enumerate(data, 1):
        pred = await selector.select(item["query"])
        ok = pred == item["expected_tool"]
        y_true.append(item["expected_tool"])
        y_pred.append(pred)
        details.append(
            {
                "query": item["query"],
                "expected": item["expected_tool"],
                "pred": pred,
                "correct": ok,
            }
        )
        logger.info(
            "[%d/%d] %s expected=%s pred=%s",
            i, len(data), item["query"][:24], item["expected_tool"], pred,
        )
    accuracy = sum(t == p for t, p in zip(y_true, y_pred)) / len(y_pred)
    from collections import Counter

    confusion = Counter((t, p) for t, p in zip(y_true, y_pred))
    report = {
        "samples": len(data),
        "accuracy": round(accuracy, 4),
        "ablate": ablate,
        "errors": [d for d in details if not d["correct"]],
        "confusion": {f"{t}->{p}": n for (t, p), n in confusion.items()},
    }
    write_report("tool_selection", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="评测入口")
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--repo", help="triage 套件的知识库键")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条(调试用)")
    parser.add_argument("--ablate", action="store_true", help="tool_selection: 模糊描述消融对照")
    args = parser.parse_args()
    if args.suite == "triage":
        if not args.repo:
            parser.error("--suite triage 需要 --repo")
        report = asyncio.run(run_triage_suite(args.repo, args.limit))
        print(f"macro-F1: {report['macro_f1']:.4f} (samples={report['samples']})")
        for lab, v in report["per_class"].items():
            print(f"  {lab}: f1={v['f1']:.4f} (support={v['support']})")
    elif args.suite == "qa":
        report = asyncio.run(run_qa_suite(args.limit))
        print(
            f"avg: correctness={report['avg_correctness']} citation_support={report['avg_citation_support']} "
            f"usefulness={report['avg_usefulness']} judge_errors={report['judge_errors']}"
        )
    else:
        report = asyncio.run(run_tool_selection_suite(args.ablate))
        print(f"tool selection accuracy: {report['accuracy']:.4f} (samples={report['samples']}, ablate={report['ablate']})")
        for error in report["errors"]:
            print(f"  x {error['query'][:30]} expected={error['expected']} pred={error['pred']}")


if __name__ == "__main__":
    main()
