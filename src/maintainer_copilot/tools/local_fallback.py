"""Rate-limit-only fallback; stale evidence is explicit and never a live API result."""
import logging
from ..config import get_settings
from ..rag.local import LocalRetriever
from .github_client import RateLimitExceeded

logger = logging.getLogger(__name__)


async def local_evidence(repo, query, retriever=None, *, path=None, issue=None):
    retriever = retriever or LocalRetriever(get_settings().local_index_path)
    try:
        docs = (retriever.lookup(repo, path=path, issue=issue)
                if isinstance(retriever, LocalRetriever) and (path is not None or issue is not None)
                else await retriever.retrieve(query, repo, top_k=50))
        if path is not None:
            docs = [d for d in docs if d["path"] == path]
        if issue is not None:
            docs = [d for d in docs if d["source"] == "issue" and d.get("meta", {}).get("issue") == issue]
        return {"degraded": True, "reason": "github_rate_limit", "source": "local_index",
                "stale": True, "docs": docs[:5], "available": bool(docs)}
    except (OSError, ValueError):
        logger.warning("local_index_unavailable repo=%s reason=github_rate_limit", repo)
        return {"degraded": True, "reason": "github_rate_limit", "source": "local_index",
                "available": False, "docs": [], "error": "local_index_unavailable"}


class ReadFallback:
    def __init__(self, client, retriever=None):
        self.client, self.retriever = client, retriever

    async def _read(self, method, repo, query, kwargs, **filters):
        try:
            return await getattr(self.client, method)(repo=repo, **kwargs)
        except RateLimitExceeded:
            logger.warning("github_rate_limit fallback=local_index method=%s repo=%s", method, repo)
            return await local_evidence(repo, query, self.retriever, **filters)

    async def get_issue(self, repo, number):
        return await self._read("get_issue", repo, f"issue {number}", {"number":number}, issue=number)

    async def search_issues(self, repo, query, per_page=30):
        return await self._read("search_issues", repo, query, {"query":query,"per_page":per_page})

    async def get_contents(self, repo, path, ref=None):
        result = await self._read("get_contents", repo, path, {"path":path,"ref":ref}, path=path)
        if isinstance(result, dict) and result.get("degraded"):
            result["requested_ref"] = ref
            result["ref_verified"] = False
        return result
