"""Read-only full-pool diagnostics. Outputs are exclusive, never overwrite history."""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

from .metrics import doc_key, retrieval_hit_at_k, recall_at_k, mrr_at_k
from maintainer_copilot.rag.retriever import HybridRetriever
from maintainer_copilot.rag.reranker import Reranker
from maintainer_copilot.workers.solver import merge_results


def relevant_positions(trace, relevant):
    by_id = {d["id"]: doc_key(d) for d in trace["candidates"]}
    stages = dict(trace["channels"])
    stages["fusion_union"] = [d["id"] for d in trace["fused"]]
    for stage in ("before_rerank", "after_rerank", "final"):
        stages[stage] = [d["id"] for d in trace[stage]]
    result = {}
    for key in sorted(relevant):
        ranks = {stage: [i for i, ident in enumerate(ids, 1) if by_id.get(ident) == key]
                 for stage, ids in stages.items()}
        if ranks["final"]:
            lost = None
        elif not ranks["fusion_union"]:
            lost = "outside_requested_channel_pools"
        elif not ranks["before_rerank"]:
            lost = "fusion_cutoff_or_dedup"
        else:
            lost = "rerank_or_final_coverage_cutoff"
        result[key] = {"ranks": ranks, "lost_stage": lost}
    return result


async def run(args):
    print("diagnostic start", flush=True)
    path = Path(args.output)
    if path.exists():
        raise FileExistsError(path)
    dataset = Path("eval/datasets/retrieval-v2.jsonl")
    data = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line]
    if args.ids:
        data = [d for d in data if d["id"] in args.ids.split(",")]
    r = HybridRetriever(database_url=os.environ["MC_TEST_DATABASE_URL"],
                        reranker=Reranker(batch_size=4))
    if args.no_rerank:
        r.reranker = None
    print("connecting to isolated database", flush=True)
    conn = await r._conn()
    try:
        cur = await conn.execute("SELECT current_database(), count(*), "
                                "md5(string_agg(md5(to_jsonb(chunks)::text),'' ORDER BY id)) FROM chunks")
        database, count, fingerprint = await cur.fetchone()
        if not database.startswith("mc_test_") or count != 6349:
            raise ValueError("Expected isolated fixed 6349-row evaluation database")
    finally:
        await conn.close()
    report = dict(started_at=datetime.now().astimezone().isoformat(),
                  code_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                  dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
                  snapshot_sha256=hashlib.sha256(Path("data/retrieval-snapshot.jsonl").read_bytes()).hexdigest(),
                  database=database, corpus_rows=count, corpus_fingerprint=fingerprint,
                  rerank=not args.no_rerank, rewrite=False, batch_size=4, items=[])
    print("corpus verified; starting retrieval", flush=True)
    start = perf_counter()
    for item in data:
        trace = {}
        docs = await r.retrieve(item["question"], item["repo"], trace=trace)
        keys = list(dict.fromkeys(doc_key(d) for d in merge_results([docs])[:8]))
        relevant = set(item["relevant"])
        scores = {"hit@3": retrieval_hit_at_k(relevant, keys, 3),
                  "hit@8": retrieval_hit_at_k(relevant, keys, 8),
                  "recall@5": recall_at_k(relevant, keys, 5),
                  "recall@8": recall_at_k(relevant, keys, 8),
                  "mrr@8": mrr_at_k(relevant, keys, 8)}
        report["items"].append(dict(id=item["id"], relevant=sorted(relevant), retrieved=keys,
                                    metrics=scores, positions=relevant_positions(trace, relevant), trace=trace))
        print(item["id"], scores, flush=True)
    report["elapsed_seconds"] = perf_counter() - start
    report["metrics"] = {key: sum(i["metrics"][key] for i in report["items"])/len(data)
                         for key in ("hit@3", "hit@8", "recall@5", "recall@8", "mrr@8")}
    with path.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(report["metrics"], flush=True)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--ids")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
