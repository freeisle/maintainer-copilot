"""一键评测：triage(分诊 macro-F1) / qa(LLM-as-judge 三维 rubric + 硬指标)
/ retrieval(检索 hit@k + MRR + Recall + 分通道诊断)。

用法:
  uv run python -m eval.run_eval --suite triage --repo eval/dubbo
  uv run python -m eval.run_eval --suite qa
  uv run python -m eval.run_eval --suite retrieval --check        # 标注核验(不打分)
  uv run python -m eval.run_eval --suite retrieval --no-rerank    # 消融: 关闭 rerank
"""
import argparse
import asyncio
import json
import hashlib
import logging
import sys
import subprocess
from importlib.metadata import version as package_version
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

SUITES = ("qa", "triage", "tool_selection", "retrieval")
MAX_SAMPLE_RETRIES = 3  # 单样本瞬时故障(LLM 连接/余额抖动)的重试上限
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
    """分诊评测: 与真实 label 类别比对 macro-F1。

    断点续跑: 每样本落盘 checkpoint(data/eval-checkpoints/), 重启跳过已完成编号。
    长跑中段(LLM 余额/数据库故障)不必从头再来。
    """
    from maintainer_copilot.workers.triage import TriageWorker

    data = load_dataset("triage")
    if limit:
        data = data[:limit]
    ckpt_path = _progress_log.parent.parent / "data" / "eval-checkpoints" / f"triage-{repo.replace('/', '__')}.jsonl"
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    done: dict[int, dict] = {}
    if ckpt_path.exists():
        for line in ckpt_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                done[item["number"]] = item
    pending = [item for item in data if item["number"] not in done]
    logger.info("断点续跑: 已完成 %d, 待跑 %d", len(done), len(pending))
    worker = TriageWorker()
    y_true: list[str] = [d["true"] for d in done.values()]
    y_pred: list[str] = [d["pred"] for d in done.values()]
    details: list[dict] = list(done.values())
    failed: list[int] = []
    for i, item in enumerate(pending, len(done) + 1):
        repo_key = item.get("repo") or repo
        r: dict | None = None
        for attempt in range(MAX_SAMPLE_RETRIES):
            try:
                r = await worker.triage(repo_key, item["title"], item["body"], item["number"])
                break
            except Exception as exc:  # noqa: BLE001 - 瞬时 LLM/网络故障不应中断长跑
                logger.warning("样本 #%s 第 %d/%d 次失败: %s", item["number"], attempt + 1, MAX_SAMPLE_RETRIES, exc)
                if attempt + 1 < MAX_SAMPLE_RETRIES:
                    await asyncio.sleep(5 * (attempt + 1))
        if r is None:
            # 不入 checkpoint: 下次续跑时重试该样本
            failed.append(item["number"])
            logger.error("样本 #%s 重试耗尽, 跳过(F1 按已分类样本计算)", item["number"])
            continue
        cat = r.get("category", "invalid")
        y_true.append(item["true_category"])
        y_pred.append(cat)
        record = {
            "number": item["number"],
            "repo": repo_key,
            "title": item["title"][:60],
            "true": item["true_category"],
            "pred": cat,
            "confidence": r.get("confidence"),
            "correct": cat == item["true_category"],
        }
        details.append(record)
        with open(ckpt_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(
            "[%d/%d] #%s true=%s pred=%s conf=%s",
            i, len(data), item["number"], item["true_category"], cat, r.get("confidence"),
        )
    report = metrics.macro_f1(y_true, y_pred)
    report["details"] = details
    report["samples"] = len(data)
    report["failed_samples"] = failed
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


async def run_retrieval_suite(
    limit: int = 0,
    k: int = 8,
    rewrite: bool = True,
    rerank: bool = True,
    check: bool = False,
    dataset: str = "retrieval",
    rerank_batch_size: int = 32,
) -> dict:
    """检索评测: 问题 -> 标注文档键的 hit@1/3/k + MRR + Recall + 分通道诊断。

    - 检索口径与线上 SolverWorker 一致: 原问题 + LLM 改写(≤2 条), 多查询检索后 merge_results
    - rerank=False 做融合排序消融; rewrite=False 走纯本地链路(无 LLM 调用)
    - check=True 只校验标注是否存在于知识库并输出 top-k(标注维护用), 不打分
    """
    from maintainer_copilot.models.llm import ModelProvider
    from maintainer_copilot.rag.query_rewriter import QueryRewriter
    from maintainer_copilot.rag.reranker import Reranker
    from maintainer_copilot.rag.retriever import HybridRetriever
    from maintainer_copilot.workers.solver import merge_results

    started_at = datetime.now().astimezone().isoformat()
    code_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    data = load_dataset(dataset)
    if limit:
        data = data[:limit]
    retriever = HybridRetriever(reranker=Reranker(batch_size=rerank_batch_size))
    if not rerank:
        # 构造器传 None 会落到默认 Reranker, 消融必须显式置空
        retriever.reranker = None
    rewriter = QueryRewriter(ModelProvider()) if rewrite else None

    async def _search(repo: str, question: str) -> list[str]:
        """按线上口径检索, 返回去重的文档键列表(保持排名)。"""
        queries = [question] + (await rewriter.rewrite(question) if rewriter else [])
        batches = [await retriever.retrieve(q, repo, top_k=k) for q in queries[:3]]
        keys: list[str] = []
        for doc in merge_results(batches)[:k]:
            key = metrics.doc_key(doc)
            if key and key not in keys:
                keys.append(key)
        return keys

    async def _channel_hits(repo: str, question: str, relevant: set[str]) -> dict:
        """分通道命中诊断: exact/dense/sparse 各自 top-k 是否命中标注。"""
        hits: dict = {}
        for name, channel in (
            ("exact", retriever._exact),
            ("dense", retriever._dense),
            ("sparse", retriever._sparse),
        ):
            try:
                ids = await channel(repo, question, k)
                docs = await retriever._fetch(repo, ids)
                hits[name] = bool({metrics.doc_key(d) for d in docs} & relevant)
                hits[name + "_documents"] = list(dict.fromkeys(metrics.doc_key(d) for d in docs))
            except Exception as exc:  # noqa: BLE001 - 单通道故障不阻断整体评测
                hits[name] = f"error: {type(exc).__name__}"
        return hits

    per_item: list[dict] = []
    for i, item in enumerate(data, 1):
        repo = item["repo"]
        question = item["question"]
        relevant = set(item.get("relevant") or [])
        retrieved = await _search(repo, question)
        if check:
            found = sorted(relevant & set(retrieved))
            missing = sorted(relevant - set(retrieved))
            per_item.append(
                {
                    "id": item.get("id"),
                    "repo": repo,
                    "question": question,
                    "labels_found": found,
                    "labels_missing": missing,
                    "top_k": retrieved,
                    "note": item.get("note", ""),
                }
            )
            logger.info(
                "[%d/%d][check] %s 标注命中 %d/%d", i, len(data), question[:30], len(found), len(relevant)
            )
            continue
        record = {
            "id": item.get("id"),
            "repo": repo,
            "question": question,
            "relevant": sorted(relevant),
            "retrieved": retrieved,
            "hit@1": metrics.retrieval_hit_at_k(relevant, retrieved, k=1),
            "hit@3": metrics.retrieval_hit_at_k(relevant, retrieved, k=3),
            f"hit@{k}": metrics.retrieval_hit_at_k(relevant, retrieved, k=k),
            "mrr": metrics.mrr_at_k(relevant, retrieved, k=k),
            "recall": metrics.recall_at_k(relevant, retrieved, k=k),
            "recall@5": metrics.recall_at_k(relevant, retrieved, k=5),
            "channel_hits": await _channel_hits(repo, question, relevant),
        }
        per_item.append(record)
        logger.info(
            "[%d/%d] %s hit@%d=%s mrr=%.2f recall=%.2f channels=%s",
            i, len(data), question[:30], k, record[f"hit@{k}"], record["mrr"], record["recall"],
            record["channel_hits"],
        )

    def avg(key: str) -> float:
        values = [r[key] for r in per_item if isinstance(r.get(key), (int, float))]
        return round(sum(values) / len(values), 4) if values else -1.0

    if check:
        report = {"mode": "check", "samples": len(data), "items": per_item}
        write_report("retrieval-check", report)
        return report
    report = {
        "dataset": dataset,
        "dataset_sha256": hashlib.sha256((DATASETS / f"{dataset}.jsonl").read_bytes()).hexdigest(),
        "code_sha": code_sha,
        "started_at": started_at,
        "backend": "PostgreSQL/pgvector + BM25 + exact + RRF",
        "embedding_model": retriever.embedder.model,
        "reranker_model": retriever.reranker.model if rerank else None,
        "rrf_k": 60,
        "rerank_pool": 50,
        "rerank_batch_size": rerank_batch_size,
        "packages": {name: package_version(name) for name in
                     ("psycopg", "pgvector", "torch", "sentence-transformers", "rank-bm25")},
        "repositories": sorted({x["repo"] for x in data}),
        "samples": len(data),
        "k": k,
        "rewrite": rewrite,
        "rerank": rerank,
        "hit@1": avg("hit@1"),
        "hit@3": avg("hit@3"),
        f"hit@{k}": avg(f"hit@{k}"),
        "mrr": avg("mrr"),
        "recall": avg("recall"),
        "recall@5": avg("recall@5"),
        "per_item": per_item,
    }
    conn = await retriever._conn()
    try:
        cur = await conn.execute("SELECT current_setting('server_version'), "
                                 "(SELECT extversion FROM pg_extension WHERE extname='vector')")
        version = await cur.fetchone()
        report["database"] = {"postgres": version[0], "pgvector": version[1]}
        cur = await conn.execute("SELECT repo,count(*),count(embedding) FROM chunks GROUP BY repo ORDER BY repo")
        report["corpus_counts"] = await cur.fetchall()
    finally:
        await conn.close()
    write_report("retrieval", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="评测入口")
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--repo", help="triage 套件的知识库键")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条(调试用)")
    parser.add_argument("--ablate", action="store_true", help="tool_selection: 模糊描述消融对照")
    parser.add_argument("--k", type=int, default=8, help="retrieval: hit@k/MRR/Recall 的截断位置")
    parser.add_argument("--no-rewrite", action="store_true", help="retrieval: 关闭 LLM 查询改写")
    parser.add_argument("--no-rerank", action="store_true", help="retrieval: 关闭 rerank(融合排序消融)")
    parser.add_argument("--rerank-batch-size", type=int, default=32, help="retrieval: 重排批大小，仅影响内存/吞吐")
    parser.add_argument("--check", action="store_true", help="retrieval: 仅校验标注是否存在于知识库")
    parser.add_argument("--dataset", choices=["retrieval", "retrieval-v2"], default="retrieval")
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
    elif args.suite == "retrieval":
        report = asyncio.run(
            run_retrieval_suite(
                limit=args.limit,
                k=args.k,
                rewrite=not args.no_rewrite,
                rerank=not args.no_rerank,
                check=args.check,
                dataset=args.dataset,
                rerank_batch_size=args.rerank_batch_size,
            )
        )
        if args.check:
            missing = [r["id"] for r in report["items"] if r["labels_missing"]]
            print(f"标注校验: {report['samples']} 条, 存在缺失标注的条目: {missing or '无'}")
        else:
            print(
                f"hit@1={report['hit@1']} hit@3={report['hit@3']} hit@{args.k}={report[f'hit@{args.k}']} "
                f"mrr={report['mrr']} recall={report['recall']} "
                f"(samples={report['samples']}, rewrite={report['rewrite']}, rerank={report['rerank']})"
            )
    else:
        report = asyncio.run(run_tool_selection_suite(args.ablate))
        print(f"tool selection accuracy: {report['accuracy']:.4f} (samples={report['samples']}, ablate={report['ablate']})")
        for error in report["errors"]:
            print(f"  x {error['query'][:30]} expected={error['expected']} pred={error['pred']}")

    from maintainer_copilot.observability import publish_evaluation
    asyncio.run(publish_evaluation(args.suite, report))


if __name__ == "__main__":
    main()
