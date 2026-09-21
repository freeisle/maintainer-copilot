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
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="评测入口")
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--repo", help="triage 套件的知识库键")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条(调试用)")
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
        print("tool_selection 套件待实现(D9+)")


if __name__ == "__main__":
    main()
