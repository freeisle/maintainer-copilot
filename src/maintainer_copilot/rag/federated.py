"""Explicit, bounded repository federation. Configuration is the trust boundary."""
import asyncio
import logging
from .lexical import diversify

logger = logging.getLogger(__name__)


class FederatedRetriever:
    def __init__(self, backend, repositories: dict[str, list[str]], timeout: float = 15):
        self.backend = backend
        self.repositories = repositories
        self.timeout = timeout

    async def retrieve(self, query, repo, top_k=8, **kwargs):
        repos = list(dict.fromkeys([repo, *self.repositories.get(repo, [])]))
        if len(repos) > 8:
            raise ValueError("Federation supports at most eight explicitly configured repositories")
        semaphore = asyncio.Semaphore(4)
        async def search(name):
            async with semaphore:
                return await asyncio.wait_for(self.backend.retrieve(query, name, top_k=top_k, **kwargs), self.timeout)
        batches = await asyncio.gather(*(search(r) for r in repos), return_exceptions=True)
        failures, ranked = [], []
        for name, result in zip(repos, batches):
            if isinstance(result, BaseException):
                failures.append({"repo":name,"error":type(result).__name__})
                logger.warning("federation_failed repo=%s error=%s", name, type(result).__name__)
                continue
            for rank, doc in enumerate(result):
                ranked.append({**doc, "repo":name, "id":f"{name}::{doc['id']}", "original_id":doc["id"],
                               "federation_score":1 / (60 + rank + 1)})
        if len(failures) == len(repos):
            raise RuntimeError("All federated retrieval sources failed")
        ranked.sort(key=lambda d: d["federation_score"], reverse=True)
        return [{**d, "retrieval_warnings":failures} for d in diversify(ranked, top_k)]


def configured_retriever():
    from ..config import get_settings
    from .local import LocalRetriever
    from .retriever import HybridRetriever
    settings = get_settings()
    backend = LocalRetriever(settings.local_index_path) if settings.retrieval_backend == "local" else HybridRetriever()
    return FederatedRetriever(backend, settings.federation_repos) if settings.federation_repos else backend
