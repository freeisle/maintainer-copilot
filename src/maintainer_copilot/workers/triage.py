"""TriageWorker 子图：Issue 分诊。

链路: 相似 issue 检索(历史 label 作弱标注参考) -> 四分类 -> label 建议 + 回复草稿
设计要点:
- 四分类: bug / feature / question / invalid
- 置信度低于阈值时降级: 只给 needs-triage 标签, 不生成回复草稿(宁可漏, 不可错)
- 回复草稿在 D7 接入 Reflector 自审与 HITL 闸门后才允许发布
"""
import json
import logging
import re
from typing import Any

from ..graph.state import AgentState
from ..models.llm import ModelProvider
from ..rag.retriever import HybridRetriever

logger = logging.getLogger(__name__)

CONFIDENCE_FLOOR = 0.6

TRIAGE_PROMPT = """你是开源仓库 {repo} 的维护者助理, 负责新 issue 的分诊。

请完成三件事:
1. 分类(四选一): bug(缺陷/异常行为) / feature(新功能请求) / question(使用求助) / invalid(信息不足/与仓库无关/纯重复)
2. 建议标签: 1-3 个简短英文标签
3. 回复草稿: 中文, 简洁友好; 信息不足时请求补充复现步骤

历史相似 issue(含其标签, 仅供参考):
{context}

新 issue:
标题: {title}
正文: {body}

只输出 JSON: {{"category": str, "labels": [str], "reply": str, "confidence": 0到1的小数}}
"""


def extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象(容忍 ```json 围栏与前后缀说明文字)。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("未找到 JSON 对象")
    return json.loads(m.group(0))


class TriageWorker:
    def __init__(self, llm=None, retriever=None) -> None:
        self.llm = llm or ModelProvider()
        self.retriever = retriever or HybridRetriever()

    async def triage(
        self, repo: str, title: str, body: str, issue_number: int | None = None
    ) -> dict:
        # 1. 相似历史 issue 检索(历史 label 是分诊的弱标注信号)
        query = f"{title} {body[:200]}"
        docs = await self.retriever.retrieve(query, repo, top_k=5, rerank_pool=25)
        issue_docs = [d for d in docs if d["source"] == "issue"][:4]
        context = (
            "\n\n".join(
                f"- 标签 {d.get('meta', {}).get('labels', [])}: {d['text'][:200]}"
                for d in issue_docs
            )
            if issue_docs
            else "无相似历史 issue"
        )
        # 2. LLM 分诊
        result = await self.llm.chat(
            [
                {
                    "role": "user",
                    "content": TRIAGE_PROMPT.format(
                        repo=repo, context=context, title=title, body=body[:1500] or "(空)"
                    ),
                }
            ],
            temperature=0.1,
        )
        try:
            parsed = extract_json(result.content)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("分诊输出不可解析, 按 invalid 降级: %s", exc)
            return {
                "category": "invalid",
                "labels": ["needs-triage"],
                "reply": None,
                "confidence": 0.0,
                "issue_number": issue_number,
                "similar_issues": [d.get("meta", {}).get("issue") for d in issue_docs],
            }
        # 3. 低置信度降级: 只打标签, 不生成回复草稿
        if float(parsed.get("confidence", 0)) < CONFIDENCE_FLOOR:
            logger.info("分诊置信度 %.2f 低于阈值, 仅打标签不生成回复", float(parsed.get("confidence", 0)))
            parsed["reply"] = None
        parsed.setdefault("labels", [])
        parsed.setdefault("category", "invalid")
        parsed["issue_number"] = issue_number
        parsed["similar_issues"] = [d.get("meta", {}).get("issue") for d in issue_docs]
        return parsed


_worker: TriageWorker | None = None


def _get_worker() -> TriageWorker:
    """模块级单例: 复用 retriever 的 BM25 索引缓存。"""
    global _worker
    if _worker is None:
        _worker = TriageWorker()
    return _worker


async def triage_node(state: AgentState) -> dict[str, Any]:
    repo = state.get("repo", "")
    issue = state.get("issue") or {}
    title = issue.get("title") or ""
    body = issue.get("body") or ""
    if not title and state.get("messages"):
        title = str(state["messages"][-1].get("content", ""))
    result = await _get_worker().triage(repo, title, body, issue.get("number"))
    return {
        # 低置信度时 draft 为空 -> Reflector 判不过 -> 走降级仅提示路径
        "draft": result.get("reply") or "",
        "draft_meta": {
            "category": result.get("category"),
            "labels": result.get("labels"),
            "confidence": result.get("confidence"),
            "issue_number": result.get("issue_number"),
            "similar_issues": result.get("similar_issues"),
        },
        "citations": [],
    }
