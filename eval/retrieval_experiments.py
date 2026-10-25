"""Fixed-corpus ablations with real PG queries and reusable actual model scores.

E0 and E2 use the original recall pool; E1 and E3 enable symbol + coverage recall.
Every strategy remains separately configurable through diagnose_retrieval.
"""
import argparse
import asyncio
import json
import gc
from time import perf_counter
from .runtime_stats import memory_bytes
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from .diagnose_retrieval import run
from .score_cache import CachedReranker
from maintainer_copilot.rag.embedder import Embedder
from maintainer_copilot.rag.retriever import HybridRetriever


class MemoEmbedder(Embedder):
    def __init__(self):
        super().__init__()
        self.vectors = {}

    def embed_query(self, text):
        if text not in self.vectors:
            self.vectors[text] = super().embed_query(text)
        return self.vectors[text]


async def matrix(output_dir, cache):
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if any((root / (name + ".json")).exists() for name in ("E0", "E1", "E2", "E3")):
        raise FileExistsError("Use a fresh experiment directory")
    revision = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    r = HybridRetriever(database_url=os.environ["MC_TEST_DATABASE_URL"], embedder=MemoEmbedder(),
                        reranker=CachedReranker(cache, revision))
    # Keep only 24 x 1024 query floats between phases, not both neural models.
    # The memo cache is process-local and never replaces actual PG vector queries.
    data = [json.loads(line) for line in Path("eval/datasets/retrieval-v2.jsonl").read_text(encoding="utf-8").splitlines() if line]
    preload_start = perf_counter()
    for item in data:
        r.embedder.embed_query(item["question"])
    preload = dict(seconds=perf_counter()-preload_start, memory_before_release=memory_bytes())
    r.embedder._local = None
    gc.collect()
    preload["memory_after_release"] = memory_bytes()
    print("Embedding phase complete", preload, flush=True)
    with (root / "model-loading.json").open("x", encoding="utf-8") as f:
        json.dump(preload, f, indent=2)
    reports = {}
    for name, recall, ranking in (("E0", False, False), ("E1", True, False),
                                  ("E2", False, True), ("E3", True, True)):
        print("Starting", name, flush=True)
        args = SimpleNamespace(output=str(root / (name + ".json")), ids=None, no_rerank=False,
                               symbol_recall=recall, coverage_pool=recall, rank_blend=ranking,
                               score_cache=cache, reranker_revision=revision)
        reports[name] = await run(args, retriever=r)
        for key in ("corpus_fingerprint", "snapshot_sha256", "dataset_sha256", "model_revisions"):
            if reports[name][key] != reports["E0"][key]:
                raise RuntimeError(f"Experiment drift: {key}")
    summary = {}
    for name, report in reports.items():
        base = reports["E0"]
        summary[name] = dict(metrics=report["metrics"],
                            delta={k: v-base["metrics"][k] for k,v in report["metrics"].items()},
                            failures=sum(i["metrics"]["hit@8"] == 0 for i in report["items"]),
                            score_cache=report["score_cache"], changes=[])
        for previous, current in zip(base["items"], report["items"], strict=True):
            delta = {k: v-previous["metrics"][k] for k,v in current["metrics"].items()}
            if any(delta.values()):
                summary[name]["changes"].append(dict(id=current["id"], delta=delta))
    with (root / "summary.json").open("x", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--score-cache", required=True)
    args = p.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(matrix(args.output_dir, args.score_cache))


if __name__ == "__main__":
    main()
