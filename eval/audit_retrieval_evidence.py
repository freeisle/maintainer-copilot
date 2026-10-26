"""Read-only source/chunk coverage and frozen challenge evidence audit."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from maintainer_copilot.rag.chunkers import CodeChunker
from scripts.ingest import CODE_SUFFIXES
from .metrics import doc_key


def audit():
    snapshot = Path("data/retrieval-snapshot.jsonl")
    rows = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads(snapshot.with_suffix(".manifest.json").read_text())
    coverage = []
    for name in ("ragent", "12306"):
        root = Path("data/repos") / name
        revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        if revision != manifest["freeisle/" + name]:
            raise ValueError("Source revision differs from fixed snapshot")
        files = subprocess.check_output(["git", "-C", str(root), "ls-files"], text=True).splitlines()
        parser = CodeChunker()
        collisions, module_docs = [], []
        for filename in files:
            path = root / filename
            if path.suffix not in CODE_SUFFIXES or not path.is_file():
                continue
            chunks = parser.split(path.read_text(encoding="utf-8", errors="replace"), filename)
            fallback = [c for c in chunks if "symbol" not in c.meta]
            docs = [c for c in chunks if c.meta.get("kind") == "module_doc"]
            if len(fallback) > 1:
                collisions.append(dict(path=filename, old_lost_parts=len(fallback)-1))
            if docs:
                module_docs.append(filename)
        coverage.append(dict(repo=name, revision=revision, fallback_collision_files=len(collisions),
                             old_lost_fallback_parts=sum(c["old_lost_parts"] for c in collisions),
                             newly_preserved_module_doc_files=len(module_docs), examples=collisions[:5]))
    dataset = Path("eval/datasets/retrieval-challenge.jsonl")
    checks = []
    for case in map(json.loads, dataset.read_text(encoding="utf-8").splitlines()):
        for key in case["relevant"]:
            needle = case.get("evidence_by_path", {}).get(key, case.get("evidence"))
            if key.startswith("issue#"):
                issues = [json.loads(line) for line in Path("data/raw/nageoffer__ragent-issues.jsonl").read_text(encoding="utf-8").splitlines()]
                issue = next(i for i in issues if "issue#" + str(i["number"]) == key)
                source = issue["title"] + " " + issue["body"]
            else:
                source = (Path("data/repos") / case["repo"].split("/")[-1] / key).read_text(encoding="utf-8")
            present = any(r["repo"] == case["repo"] and doc_key(r) == key and needle in r["text"] for r in rows)
            if needle not in source or not present:
                raise ValueError(f"Unverified evidence: {case['id']} {key}")
            checks.append(dict(id=case["id"], path=key, source_contains=True, snapshot_contains=True,
                               source_sha256=hashlib.sha256(source.encode()).hexdigest()))
    return dict(snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                challenge_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
                source_coverage=coverage, challenge_evidence=checks,
                note="Read-only source-side audit. No index rebuild or retrieval-quality claim.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    report = audit()
    with Path(args.output).open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(report)
