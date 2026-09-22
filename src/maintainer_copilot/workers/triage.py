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

from ..graph.state import AgentState, message_text
from ..models.llm import ModelProvider
from ..rag.retriever import HybridRetriever

logger = logging.getLogger(__name__)

CONFIDENCE_FLOOR = 0.6

TRIAGE_PROMPT = """你是开源仓库 {repo} 的维护者助理, 负责新 issue 的分诊。

分类判别规则(按顺序执行):
1. 先找"可复现的错误行为": 报错日志/异常堆栈/复现步骤中出现错误输出或非预期结果 → bug
2. 没有错误行为时看诉求:
   - 请求新增/改变行为(支持 X/should/建议/优化/性能风险/希望) → feature;
     注意: 标题以 [Bug] 开头只是 issue 模板前缀, 不代表类别, 一律以正文诉求为准
   - 使用求助(怎么做/怎么配置/是什么) → question
   - 信息不足以理解问题、与仓库无关、纯重复 → invalid
3. 反例(few-shot, 以下均为 feature 而非 bug):
   - 标题 "[Bug] Dubbo3 Triple protocol not support GRPC backpressure",
     正文只描述"希望 Triple 协议支持 GRPC backpressure", 无错误日志 → feature
   - 标题 "[Bug] BatchExecutorQueue performance risk",
     正文分析性能风险并建议改进, 未报告错误输出 → feature
   - 正文以疑问句询问"为什么某行为缺失/能否支持 X", 未发生任何错误 → feature(请求行为改变)

请完成三件事:
1. 分类(四选一): bug / feature / question / invalid
2. 建议标签: 1-3 个简短英文标签
3. 回复草稿(中文, 简洁友好), 必须包含:
   a. 分类结论与理由(一句话)
   b. 优先级建议(低/中/高)与下一步动作(请求复现信息/建议提交方向)
   c. 每条引用资料的具体结论标注编号 [n](n 对应下方资料编号)
   硬性禁止:
   - 推测维护者的态度或承诺处理时间; 后续动作一律用条件性表述("若确认…可以…")
   - 引用用户未提供的事实(如"你已覆盖 4 个测试用例"这类编造)
   - 引用资料中不存在的代码/配置细节而不标注出处

检索资料:
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
        self, repo: str, title: str, body: str, issue_number: int | None = None, advice: str = ""
    ) -> dict:
        # 1. 混合检索: 历史 issue(弱标注信号)优先, 代码块次之; 全部编号化供草稿引用
        query = f"{title} {body[:200]}"
        docs = await self.retriever.retrieve(query, repo, top_k=8, rerank_pool=25)
        issue_docs = [d for d in docs if d["source"] == "issue"][:4]
        code_docs = [d for d in docs if d["source"] == "code"][:2]
        context_docs = issue_docs + code_docs
        context = (
            "\n\n".join(
                f"[{i}] ({d['source']}:{d['path']}) {d['text'][:250]}"
                for i, d in enumerate(context_docs, 1)
            )
            if context_docs
            else "无相似历史资料"
        )
        context_citations = [
            {
                "source": d["source"],
                "path": d["path"],
                "snippet": d["text"][:300],
            }
            for d in context_docs
        ]
        # 2. LLM 分诊(重写时附上自审建议)
        advice_note = f"\n\n上一稿被驳回, 修改建议: {advice}" if advice else ""
        result = await self.llm.chat(
            [
                {
                    "role": "user",
                    "content": TRIAGE_PROMPT.format(
                        repo=repo, context=context, title=title, body=body[:1500] or "(空)"
                    )
                    + advice_note,
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
                "context_citations": context_citations,
            }
        # 3. 低置信度降级: 只打标签, 不生成回复草稿
        if float(parsed.get("confidence", 0)) < CONFIDENCE_FLOOR:
            logger.info("分诊置信度 %.2f 低于阈值, 仅打标签不生成回复", float(parsed.get("confidence", 0)))
            parsed["reply"] = None
        logger.info(
            "分诊完成: category=%s confidence=%s reply长度=%d",
            parsed.get("category"), parsed.get("confidence"), len(parsed.get("reply") or ""),
        )
        parsed.setdefault("labels", [])
        parsed.setdefault("category", "invalid")
        parsed["issue_number"] = issue_number
        parsed["similar_issues"] = [d.get("meta", {}).get("issue") for d in issue_docs]
        parsed["context_citations"] = context_citations
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
        title = message_text(state["messages"][-1])
    result = await _get_worker().triage(
        repo, title, body, issue.get("number"), advice=state.get("reflect_advice", "")
    )
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
        "citations": result.get("context_citations") or [],
    }
