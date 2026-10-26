"""Small uncached CPU profile; never interpret score-cache replay as inference."""
import argparse
import asyncio
import gc
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path
from time import perf_counter

from eval.retrieval_experiments import MemoEmbedder
from eval.runtime_stats import memory_bytes, local_revision
from maintainer_copilot.rag.retriever import HybridRetriever
from maintainer_copilot.rag.reranker import Reranker


async def profile(output, reference_cache=None):
    path = Path(output)
    if path.exists():
        raise FileExistsError(path)
    dataset = Path("eval/datasets/retrieval-v2.jsonl")
    items = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    items = [item for item in items if item["id"] in ("ret-009", "ret-015")]
    embedder = MemoEmbedder()
    start = perf_counter()
    embedder._load_local()
    embedding_load = perf_counter() - start
    encode = []
    for item in items:
        start = perf_counter()
        embedder.embed_query(item["question"])
        encode.append(perf_counter() - start)
    embedding_memory = memory_bytes()
    embedder._local = None
    gc.collect()
    reranker = Reranker(batch_size=4, revision="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
    start = perf_counter()
    model = reranker._load()
    reranker_load = perf_counter() - start
    r = HybridRetriever(database_url=os.environ["MC_TEST_DATABASE_URL"], embedder=embedder, reranker=reranker)
    conn = await r._conn()
    try:
        cur = await conn.execute("SELECT current_database(),count(*) FROM chunks")
        db, count = await cur.fetchone()
        if not db.startswith("mc_test_") or count != 6349:
            raise ValueError("Expected isolated fixed evaluation database")
    finally:
        await conn.close()
    from .score_cache import CachedReranker
    reference = CachedReranker(reference_cache, "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e") if reference_cache else None
    rows = []
    for batch, pool in ((4, 20), (1, 20), (4, 50)):
        reranker.batch_size = batch
        for item in items:
            trace = {}
            await r.retrieve(item["question"], item["repo"], rerank_pool=pool, trace=trace)
            rows.append(dict(id=item["id"], batch_size=batch, pool=pool,
                             timings=trace["timings"], memory=memory_bytes(),
                             candidates=len(trace["before_rerank"])))
            if reference:
                texts = {d["id"]: d["text"] for d in await r._fetch(item["repo"], [d["id"] for d in trace["after_rerank"]])}
                differences = [abs(d["rerank_score"] - reference.cache[key])
                               for d in trace["after_rerank"]
                               if (key := reference.key(item["question"], texts[d["id"]])) in reference.cache]
                rows[-1]["reference_comparison"] = dict(matched=len(differences),
                    max_absolute_score_difference=max(differences) if differences else None)
            print(rows[-1], flush=True)
    report = dict(code_sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
                  platform=platform.platform(), samples=2, repeats_per_configuration=1,
                  no_score_cache=True, query_vectors_precomputed=True,
                  cold_embedding_load_seconds=embedding_load, warm_query_encode_seconds=encode,
                  cold_reranker_load_seconds=reranker_load, embedding_phase_memory=embedding_memory,
                  model_revisions={name:local_revision(name) for name in ("BAAI/bge-m3","BAAI/bge-reranker-v2-m3")},
                  dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
                  model_max_length=model.max_length, model_dtype=str(next(model.parameters()).dtype),
                  model_device=str(next(model.parameters()).device), rows=rows, summary=[])
    for batch,pool in ((4,20),(1,20),(4,50)):
        values=sorted(row["timings"]["total"] for row in rows if row["batch_size"]==batch and row["pool"]==pool)
        report["summary"].append(dict(batch_size=batch,pool=pool,n=len(values),p50=statistics.median(values),
                                      p95_nearest_rank=values[-1]))
    report["limitations"] = "Two queries, one repetition per config; P95 is the maximum of two values. Peak RSS is process lifetime cumulative, not a per-batch isolated peak. First repo query includes BM25 initialization. Encoding measured separately; total is SQL+lexical+CE with precomputed query vectors. No load-test or batch32 claim."
    with path.open("x",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2)


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--output",required=True)
    parser.add_argument("--reference-cache")
    args=parser.parse_args()
    if sys.platform=="win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(profile(args.output, args.reference_cache))


if __name__=="__main__":
    main()
