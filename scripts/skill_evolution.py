"""Weekly candidate generation and explicit local HITL review (no auto-approval)."""
import argparse
import asyncio
import json
from maintainer_copilot.skills.evolution import EvolutionStore
from maintainer_copilot.models.llm import ModelProvider


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["generate","pending","approve","reject","history","rollback"])
    p.add_argument("--repo")
    p.add_argument("--id")
    p.add_argument("--reviewer")
    p.add_argument("--version", type=int)
    p.add_argument("--note", default="")
    a = p.parse_args()
    store = EvolutionStore()
    if a.action in ("generate","history","rollback") and not a.repo:
        p.error("--repo required")
    if a.action in ("approve","reject","rollback") and not a.reviewer:
        p.error("--reviewer required: read pending candidate and evidence before deciding")
    if a.action in ("approve","reject") and not a.id:
        p.error("--id required")
    if a.action == "rollback" and a.version is None:
        p.error("--version required (0 disables overlay)")
    if a.action == "generate":
        result = asyncio.run(store.generate(a.repo, ModelProvider()))
    elif a.action == "pending":
        result = store.pending()
    elif a.action == "history":
        result = store.history(a.repo)
    elif a.action == "rollback":
        result = store.rollback(a.repo,a.version,a.reviewer)
    else:
        result = store.decide(a.id,"approved" if a.action == "approve" else "rejected",a.reviewer,a.note)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
