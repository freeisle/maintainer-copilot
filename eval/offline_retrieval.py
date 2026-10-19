"""Reproducible lexical ablation. This is NOT the historical hybrid baseline.

Build a source snapshot: --build; later evaluate the exact snapshot without it.
Source trees and issue exports are local prerequisites; nothing is downloaded.
"""
import argparse
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

from maintainer_copilot.rag.chunkers import CodeChunker, DocChunker, IssueChunker
from maintainer_copilot.rag.indexer import chunk_id
from maintainer_copilot.rag.local import LocalRetriever
from scripts.ingest import CODE_SUFFIXES, DOC_SUFFIXES
from .metrics import doc_key, retrieval_hit_at_k, recall_at_k, mrr_at_k

ROOTS = {"freeisle/ragent": Path("data/repos/ragent"), "freeisle/12306": Path("data/repos/12306"),
         "local/maintainer-copilot": Path(".")}


def build_snapshot(path: Path):
    rows, revisions = {}, {}
    for repo, root in ROOTS.items():
        files = subprocess.check_output(["git", "-C", str(root), "ls-files"], text=True).splitlines()
        revisions[repo] = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        for name in files:
            # Current project eval questions must never leak into the searchable corpus.
            if repo.startswith("local/") and not name.startswith("src/"):
                continue
            file = root / name
            if not file.is_file() or file.suffix not in CODE_SUFFIXES | DOC_SUFFIXES:
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
            chunker = CodeChunker() if file.suffix in CODE_SUFFIXES else DocChunker()
            for chunk in chunker.split(text, name):
                cid = chunk_id(repo, chunk)
                rows[cid] = dict(id=cid, repo=repo, source=chunk.source, path=name, text=chunk.text, meta=chunk.meta)
    for repo, filename in [("freeisle/ragent", "nageoffer__ragent-issues.jsonl"), ("freeisle/12306", "nageoffer__12306-issues.jsonl")]:
        file = Path("data/raw") / filename
        if file.exists():
            for line in file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    for chunk in IssueChunker().split(json.loads(line)):
                        cid = chunk_id(repo, chunk)
                        rows[cid] = dict(id=cid, repo=repo, source="issue", path=str(chunk.meta.get("path", "")), text=chunk.text, meta=chunk.meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(rows[k], ensure_ascii=False) + "\n" for k in sorted(rows)), encoding="utf-8")
    path.with_suffix(".manifest.json").write_text(json.dumps(revisions, indent=2), encoding="utf-8")


async def evaluate(path: Path):
    dataset = Path("eval/datasets/retrieval-v2.jsonl")
    cases = [json.loads(x) for x in dataset.read_text(encoding="utf-8").splitlines()]
    report = dict(mode="offline lexical only; no dense/rewrite/reranker", samples=len(cases),
                  snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(), variants={})
    manifest = path.with_suffix(".manifest.json")
    report["source_revisions"] = json.loads(manifest.read_text()) if manifest.exists() else {}
    corpus = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    keys = {(x["repo"], doc_key(x)) for x in corpus}
    report["chunks"] = len(corpus)
    report["missing_labels"] = {x["id"]: [k for k in x["relevant"] if (x["repo"], k) not in keys]
                                for x in cases if any((x["repo"], k) not in keys for k in x["relevant"])}
    for enhanced in (False, True):
        retriever = LocalRetriever(path, enhanced)
        results = []
        for item in cases:
            docs = await retriever.retrieve(item["question"], item["repo"], 8)
            keys = list(dict.fromkeys(doc_key(d) for d in docs))
            relevant = set(item["relevant"])
            results.append(dict(id=item["id"], retrieved=keys,
                                hit3=retrieval_hit_at_k(relevant, keys, 3),
                                recall5=recall_at_k(relevant, keys, 5), mrr=mrr_at_k(relevant, keys, 8)))
        report["variants"]["enhanced" if enhanced else "legacy"] = {
            **{k: round(sum(r[k] for r in results) / len(results), 4) for k in ("hit3", "recall5", "mrr")},
            "results": results}
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build", action="store_true")
    p.add_argument("--snapshot", type=Path, default=Path("data/retrieval-snapshot.jsonl"))
    p.add_argument("--report", type=Path, default=Path("eval/reports/retrieval-offline-v2.json"))
    args = p.parse_args()
    if args.build:
        build_snapshot(args.snapshot)
    report = asyncio.run(evaluate(args.snapshot))
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({v: {k: x for k, x in r.items() if k != "results"} for v, r in report["variants"].items()})
