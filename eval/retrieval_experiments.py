"""Fixed-corpus ablations with real PG queries and reusable actual model scores.

E0 and E2 use the original recall pool; E1 and E3 enable symbol + coverage recall.
Every strategy remains separately configurable through diagnose_retrieval.
"""
import argparse
import asyncio
import json
import gc
import subprocess
import hashlib
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
        super().__init__(revision="5617a9f61b028005a4858fdac845db406aefb181")
        self.vectors = {}

    def embed_query(self, text):
        if text not in self.vectors:
            self.vectors[text] = super().embed_query(text)
        return self.vectors[text]


async def matrix(output_dir, cache, dataset="retrieval-v2", experiments="E0,E1,E2,E3"):
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("Commit/review working changes before running the fixed experiment")
    source_files = sorted(Path("src").rglob("*.py")) + sorted(Path("eval").glob("*.py"))
    def source_hash():
        return hashlib.sha256(b"".join(p.read_bytes() for p in source_files)).hexdigest()
    initial_source = source_hash()
    configurations = {"E0": (False, False, False), "E1": (True, True, False),
                      "E2": (False, False, True), "E3": (True, True, True),
                      "E1-symbol": (True, False, False), "E1-coverage": (False, True, False)}
    names = experiments.split(",")
    if not names or names[0] != "E0" or len(set(names)) != len(names) or any(n not in configurations for n in names):
        raise ValueError("Select distinct known experiments, starting with E0")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if any((root / (name + ".json")).exists() for name in names):
        raise FileExistsError("Use a fresh experiment directory")
    revision = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    r = HybridRetriever(database_url=os.environ["MC_TEST_DATABASE_URL"], embedder=MemoEmbedder(),
                        reranker=CachedReranker(cache, revision))
    # Keep only 24 x 1024 query floats between phases, not both neural models.
    # The memo cache is process-local and never replaces actual PG vector queries.
    data = [json.loads(line) for line in (Path("eval/datasets") / (dataset + ".jsonl")).read_text(encoding="utf-8").splitlines() if line]
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
    for name in names:
        symbol, coverage, ranking = configurations[name]
        if source_hash() != initial_source:
            raise RuntimeError("Source changed during experiment")
        print("Starting", name, flush=True)
        args = SimpleNamespace(output=str(root / (name + ".json")), ids=None, no_rerank=False,
                               symbol_recall=symbol, coverage_pool=coverage, rank_blend=ranking,
                               score_cache=cache, reranker_revision=revision, dataset=dataset)
        reports[name] = await run(args, retriever=r)
        if source_hash() != initial_source:
            raise RuntimeError("Source changed during experiment; do not accept results")
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
    p.add_argument("--dataset", choices=["retrieval-v2", "retrieval-challenge"], default="retrieval-v2")
    p.add_argument("--experiments", default="E0,E1,E2,E3")
    args = p.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(matrix(args.output_dir, args.score_cache, args.dataset, args.experiments))


if __name__ == "__main__":
    main()
