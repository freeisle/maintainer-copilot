import asyncio
import json
from types import SimpleNamespace
import pytest
from maintainer_copilot.rag.local import LocalRetriever
from maintainer_copilot.rag.federated import FederatedRetriever
from maintainer_copilot.workers.solver import SolverWorker


@pytest.mark.asyncio
async def test_federated_solver_preserves_source_and_scope(tmp_path):
    path = tmp_path / "snapshot"
    rows = [dict(repo=repo, id=str(i), path=f"{i}.md", source="doc", text=t) for repo in ["a/b","c/d","private/repo"]
            for i,t in enumerate(["needle context", "unrelated", "other"])]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    retriever = FederatedRetriever(LocalRetriever(path), {"a/b":["c/d"]})
    docs = await retriever.retrieve("needle", "a/b")
    assert {d["repo"] for d in docs} == {"a/b","c/d"}
    assert len({d["id"] for d in docs}) == 2
    class LLM:
        async def chat(self, messages, **kwargs):
            return SimpleNamespace(content="from both [1][2]")
    worker = SolverWorker(llm=LLM(),retriever=retriever,prefs_getter=lambda *x:None,skill_getter=lambda repo:"")
    result = await worker.solve("needle", "a/b")
    assert {c["repo"] for c in result["citations"]} == {"a/b","c/d"}


@pytest.mark.asyncio
async def test_partial_failure_timeout_and_total_failure():
    class Backend:
        async def retrieve(self, query, repo, **kwargs):
            if repo == "slow":
                await asyncio.sleep(1)
            if repo == "bad":
                raise OSError("unavailable")
            return [dict(id="1",source="doc",path="p",text="text")]
    retriever = FederatedRetriever(Backend(), {"good":["slow","bad"]},timeout=.01)
    docs = await retriever.retrieve("q", "good")
    assert {x["repo"] for x in docs[0]["retrieval_warnings"]} == {"slow","bad"}
    with pytest.raises(RuntimeError):
        await retriever.retrieve("q","bad")
