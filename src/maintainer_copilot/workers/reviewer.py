"""ReviewWorker 子图：PR 初审。

链路: 拉取 PR 元信息 + diff -> 解析改动文件 -> 混合检索相关代码/文档
    -> 四维初审意见(带 [n] 引用) -> Reflector 自审 -> HumanGate -> Executor

设计要点:
- 意见只依据 diff 与检索资料, 引用之外不得下结论(与 Solver 同一红线)
- diff 过大时截断并进入"只总结"模式: 只做文件级风险提示, 不逐行判错(降级路径)
- 写操作仍只经 Executor(评论到 PR 走 issues 评论端点)
"""
import logging
import re
from typing import Any

from ..config import get_settings
from ..graph.state import AgentState
from ..models.llm import ModelProvider
from ..rag.retriever import HybridRetriever
from ..tools.github_client import GitHubClient

logger = logging.getLogger(__name__)

DIFF_HARD_LIMIT = 20000  # 硬上限(字符): 超出即截断
DIFF_SUMMARY_LIMIT = 8000  # 超出进入"只总结"模式, 不做逐行判断

REVIEW_PROMPT = """你是开源仓库 {repo} 的维护者助理, 负责对 PR 做初审(供维护者参考, 不直接拒绝)。

按四个维度审查, 每条具体意见必须标注引用编号 [n](对应下方检索资料编号):
1. 正确性: 逻辑错误/边界条件/空值风险/并发问题
2. 规范: 命名/风格/与仓库既有约定的一致性(以检索到的既有代码为基准)
3. 测试覆盖: 新增行为是否可测/缺少哪些测试
4. 风险: 性能/兼容性/安全/迁移影响

硬规则:
- 只依据下方检索资料与 diff 内容下结论, 严禁引用之外的具体事实
- 具体意见尽量给出 diff 中"文件:行号"位置(行号以 diff 中的 @@ 标注为准)
- 没有发现某类问题就明确跳过该维度, 不硬凑
- 结论用条件性表述("建议确认…"), 不替维护者做决定

{summary_mode}

检索资料:
{context}

PR 信息:
标题: {title}
说明: {body}
改动文件: {changed_files}

diff:
{diff}

输出中文, 分点简洁。
"""


def parse_diff_files(diff: str) -> list[str]:
    """从 unified diff 提取改动文件路径(diff --git a/x b/y 行)。"""
    return re.findall(r"^diff --git a/(\S+) b/", diff, re.MULTILINE)


class ReviewWorker:
    def __init__(self, llm=None, retriever=None, github=None, max_context_chunks: int = 6) -> None:
        self.llm = llm or ModelProvider()
        self.retriever = retriever or HybridRetriever()
        self.github = github or GitHubClient(token=get_settings().github_token)
        self.max_context_chunks = max_context_chunks

    async def review(self, repo: str, pr_number: int, advice: str = "") -> dict:
        # 1. 拉取 PR 元信息与 diff
        pr = await self.github.get_pull_request(repo, pr_number)
        diff = await self.github.get_pull_diff(repo, pr_number)
        changed_files = parse_diff_files(diff)
        truncated = len(diff) > DIFF_HARD_LIMIT
        diff_text = diff[:DIFF_HARD_LIMIT]
        summary_mode = truncated or len(diff) > DIFF_SUMMARY_LIMIT
        # 2. 检索相关代码/文档: 标题 + 说明 + 改动文件路径作查询
        query = f"{pr.get('title', '')} {pr.get('body', '')[:200]} {' '.join(changed_files[:6])}"
        docs = await self.retriever.retrieve(query, repo, top_k=self.max_context_chunks)
        context = (
            "\n\n".join(
                f"[{i}] ({d['source']}:{d['path']}) {d['text'][:500]}"
                for i, d in enumerate(docs, 1)
            )
            if docs
            else "无相关检索资料(仅依据 diff 本身审查)"
        )
        citations = [
            {"source": d["source"], "path": d["path"], "snippet": d["text"][:300]}
            for d in docs
        ]
        # 3. 生成初审意见(重写时附上自审建议)
        advice_note = f"\n\n上一稿被驳回, 修改建议: {advice}" if advice else ""
        summary_note = ""
        if summary_mode:
            summary_note = (
                f"注意: diff 过长已截断(原始 {len(diff)} 字符, 仅展示 {DIFF_HARD_LIMIT} 字符),"
                " 只做文件级总结与风险提示, 不逐行判错。"
            )
        prompt = REVIEW_PROMPT.format(
            repo=repo,
            summary_mode=summary_note,
            context=context,
            title=pr.get("title", ""),
            body=(pr.get("body") or "")[:800],
            changed_files=", ".join(changed_files) or "(未解析到)",
            diff=diff_text,
        )
        result = await self.llm.chat(
            [{"role": "user", "content": prompt + advice_note}], temperature=0.1
        )
        return {
            "draft": result.content,
            "citations": citations,
            "docs": docs,
            "pr_meta": {
                "issue_number": pr_number,  # 评论走 issues 端点, 复用 draft_meta 约定
                "labels": [],
                "changed_files": changed_files,
                "pr_title": pr.get("title", ""),
            },
            "truncated": truncated,
            "summary_mode": summary_mode,
        }


_worker: ReviewWorker | None = None


def _get_worker() -> ReviewWorker:
    global _worker
    if _worker is None:
        _worker = ReviewWorker()
    return _worker


async def reviewer_node(state: AgentState) -> dict[str, Any]:
    repo = state.get("repo", "")
    pr_number = (state.get("pr") or {}).get("number") or (state.get("issue") or {}).get("number")
    if not pr_number:
        return {
            "draft": "依据不足: 未提供 PR 编号, 无法拉取 diff。",
            "draft_meta": {},
            "citations": [],
        }
    result = await _get_worker().review(repo, int(pr_number), advice=state.get("reflect_advice", ""))
    return {
        "draft": result["draft"],
        "draft_meta": result["pr_meta"],
        "citations": result["citations"],
    }
