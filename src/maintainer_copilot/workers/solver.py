"""SolverWorker 子图：代码库问答。

链路: QueryRewrite(LLM 术语补全) -> Retrieve(混合检索) -> Answer(带引用) -> 引用校验
设计要点:
- 回答只能引用检索到的 chunk, 引用标记 [n] 与 citations 列表一一对应
- 检索不到足够信息时明确回答"依据不足", 不允许编造
- 引用精确率是程序硬指标, 供评测回归
"""
import logging
import re
from typing import Any, Callable

from ..graph.state import AgentState, message_text
from ..memory.long_term import get_preference
from ..models.llm import ModelProvider
from ..rag.query_rewriter import QueryRewriter
from ..rag.retriever import HybridRetriever

logger = logging.getLogger(__name__)

REFUSAL_TEXT = (
    "依据不足: 检索到的资料未覆盖该问题。"
    "建议换用更具体的术语重新提问, 或直接查阅仓库文档与源码。"
)

ANSWER_PROMPT = """你是开源仓库 {repo} 的维护者助理。只依据下方检索到的资料回答, 不得使用资料之外的知识。

硬规则:
1. 每个事实性陈述(代码行为/数字/配置项/版本等)必须紧跟引用编号 [n](对应资料编号);
   没有编号的句子只能是对资料的一般性概括或后续行动建议。
2. 严禁输出引用之外的具体事实: 资料未写明的行为、数值、配置项一律不得出现在回答中。
3. 资料不足或未覆盖问题核心时, 只输出以下固定拒绝话术(不猜测、不附带任何具体结论):

{refusal}
{language_note}
{context}

用户问题: {question}
"""


def build_answer_prompt(
    repo: str, context: str, question: str, language_note: str = ""
) -> str:
    """渲染 Answer Prompt(独立函数便于单测硬规则, 防止后续改动悄悄丢失约束)。"""
    return ANSWER_PROMPT.format(
        repo=repo,
        context=context,
        question=question,
        refusal=REFUSAL_TEXT,
        language_note=language_note,
    )


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
    def __init__(
        self,
        llm=None,
        retriever=None,
        max_context_chunks: int = 8,
        prefs_getter: Callable | None = None,
    ) -> None:
        self.llm = llm or ModelProvider()
        self.retriever = retriever or HybridRetriever()
        self.rewriter = QueryRewriter(self.llm)
        self.max_context_chunks = max_context_chunks
        self.prefs_getter = prefs_getter or get_preference

    async def solve(self, question: str, repo: str, advice: str = "") -> dict:
        # 1. 查询改写: 原问题 + LLM 改写(术语补全/中英变体), 最多 3 条
        queries = [question] + await self.rewriter.rewrite(question)
        # 2. 多查询混合检索
        batches = []
        for q in queries[:3]:
            batches.append(await self.retriever.retrieve(q, repo, top_k=self.max_context_chunks))
        docs = merge_results(batches)[: self.max_context_chunks]
        if not docs:
            return {"draft": REFUSAL_TEXT, "citations": [], "docs": []}
        # 3. 生成带引用回答(重写时附上自审建议; 注入长期记忆偏好如回复语言)
        advice_note = f"\n\n上一稿被驳回, 修改建议: {advice}" if advice else ""
        language_note = ""
        try:
            language = self.prefs_getter(repo, "reply_language")
            if language:
                language_note = f"\n偏好要求: 回复必须使用语言: {language}。"
        except Exception as exc:  # noqa: BLE001 - 偏好读取失败不阻断回答
            logger.warning("读取回复语言偏好失败, 用默认: %s", exc)
        context = "\n\n".join(
            f"[{i}] ({d['source']}:{d['path']}) {d['text'][:600]}"
            for i, d in enumerate(docs, 1)
        )
        prompt = build_answer_prompt(repo, context, question, language_note)
        result = await self.llm.chat(
            [{"role": "user", "content": prompt + advice_note}],
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
    question = message_text(state["messages"][-1]) if state.get("messages") else ""
    repo = state.get("repo", "")
    result = await _get_worker().solve(question, repo, advice=state.get("reflect_advice", ""))
    return {"draft": result["draft"], "citations": result["citations"]}
