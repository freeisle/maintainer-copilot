"""SolverWorker 子图：代码库问答。

链路: QueryRewrite(LLM 术语补全) -> Retrieve(混合检索) -> Answer(带引用) -> 引用校验
设计要点:
- 回答只能引用检索到的 chunk, 引用标记 [n] 与 citations 列表一一对应
- 检索不到足够信息时明确回答"依据不足", 不允许编造
- 引用精确率是程序硬指标, 供评测回归
"""
import logging
import re
from typing import Any

from ..graph.state import AgentState
from ..models.llm import ModelProvider
from ..rag.query_rewriter import QueryRewriter
from ..rag.retriever import HybridRetriever

logger = logging.getLogger(__name__)

ANSWER_PROMPT = """你是开源仓库 {repo} 的维护者助理。请只依据下方检索到的资料回答用户问题。

规则:
1. 每条结论后标注引用编号, 格式 [1] [2](对应资料编号)
2. 资料不足以回答时, 明确说明"依据不足", 并给出可查阅的方向
3. 禁止使用检索资料以外的知识编造事实

{context}

用户问题: {question}
"""


def parse_citations(answer: str) -> list[int]:
    """解析回答中的引用编号, 去重并保持出现顺序。"""
    return list(dict.fromkeys(int(n) for n in re.findall(r"\[(\d+)\]", answer)))


def merge_results(batches: list[list[dict]]) -> list[dict]:
    """合并多轮检索结果: 按 id 去重, 保留最高 rerank 分, 降序返回。"""
    merged: dict[str, dict] = {}
    for batch in batches:
        for item in batch:
            prev = merged.get(item["id"])
            if prev is None or item.get("rerank_score", 0) > prev.get("rerank_score", 0):
                merged[item["id"]] = item
    return sorted(merged.values(), key=lambda x: x.get("rerank_score", 0), reverse=True)


class SolverWorker:
    def __init__(self, llm=None, retriever=None, max_context_chunks: int = 8) -> None:
        self.llm = llm or ModelProvider()
        self.retriever = retriever or HybridRetriever()
        self.rewriter = QueryRewriter(self.llm)
        self.max_context_chunks = max_context_chunks

    async def solve(self, question: str, repo: str) -> dict:
        # 1. 查询改写: 原问题 + LLM 改写(术语补全/中英变体), 最多 3 条
        queries = [question] + await self.rewriter.rewrite(question)
        # 2. 多查询混合检索
        batches = []
        for q in queries[:3]:
            batches.append(await self.retriever.retrieve(q, repo, top_k=self.max_context_chunks))
        docs = merge_results(batches)[: self.max_context_chunks]
        if not docs:
            return {"draft": "依据不足: 未在知识库中检索到相关内容。", "citations": [], "docs": []}
        # 3. 生成带引用回答
        context = "\n\n".join(
            f"[{i}] ({d['source']}:{d['path']}) {d['text'][:600]}"
            for i, d in enumerate(docs, 1)
        )
        result = await self.llm.chat(
            [
                {
                    "role": "user",
                    "content": ANSWER_PROMPT.format(repo=repo, context=context, question=question),
                }
            ],
            temperature=0.1,
        )
        answer = result.content
        # 4. 引用校验: 只保留编号有效的引用
        valid_cited = [n for n in parse_citations(answer) if 1 <= n <= len(docs)]
        citations = [
            {
                "source": docs[n - 1]["source"],
                "path": docs[n - 1]["path"],
                "snippet": docs[n - 1]["text"][:300],
            }
            for n in valid_cited
        ]
        return {"draft": answer, "citations": citations, "docs": docs}


_worker: SolverWorker | None = None


def _get_worker() -> SolverWorker:
    """模块级单例: retriever 的 BM25 索引按进程缓存, 避免每次节点调用重建。"""
    global _worker
    if _worker is None:
        _worker = SolverWorker()
    return _worker


async def solver_node(state: AgentState) -> dict[str, Any]:
    question = str(state["messages"][-1].get("content", "")) if state.get("messages") else ""
    repo = state.get("repo", "")
    result = await _get_worker().solve(question, repo)
    return {"draft": result["draft"], "citations": result["citations"]}
