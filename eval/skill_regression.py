"""Compare a pending FAQ against the active skill without publishing it.

Uses the real Solver/QA dataset and independent citation hard metrics. Requires
the configured LLM and retrieval backend. Reviewer inspects the generated report.
"""
import argparse
import asyncio
import json
from pathlib import Path

from maintainer_copilot.skills.evolution import EvolutionStore
from maintainer_copilot.skills.loader import skill_context
from maintainer_copilot.workers.solver import SolverWorker
from .metrics import citation_precision


async def compare(cid, worker_factory=SolverWorker):
    store = EvolutionStore()
    with store.conn() as c:
        candidate = c.execute("SELECT * FROM skill_candidates WHERE id=?", (cid,)).fetchone()
    if candidate is None or candidate["status"] != "pending":
        raise ValueError("Pending candidate required")
    cases = [json.loads(x) for x in Path("eval/datasets/qa.jsonl").read_text(encoding="utf-8").splitlines()]
    cases = [x for x in cases if x["repo"] == candidate["repo"]]
    if not cases:
        raise ValueError("No QA regression cases for this repository")
    before = skill_context(candidate["repo"])
    results = []
    for mode, context in [("baseline", before), ("candidate", before + "\n" + candidate["content"])]:
        worker = worker_factory(skill_getter=lambda repo, text=context: text)
        for case in cases:
            result = await worker.solve(case["question"], case["repo"])
            results.append(dict(mode=mode, question=case["question"], answer=result["draft"],
                                citation_precision=citation_precision([x["snippet"] for x in result["citations"]],result["draft"])))
    return {"candidate":cid, "repo":candidate["repo"], "results":results,
            "note":"Hard citation scores are not semantic correctness; maintainer review required."}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.write_text(json.dumps(asyncio.run(compare(a.id)),ensure_ascii=False,indent=2),encoding="utf-8")
