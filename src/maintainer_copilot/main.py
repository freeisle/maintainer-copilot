"""CLI 入口。

用法:
  uv run mc chat                     # 交互式问答(直连 LLM, POC)
  uv run mc serve                    # 启动 FastAPI(审核台 + webhook)
  uv run mc mcp                      # MCP stdio 模式启动工具服务
  uv run mc demo-triage owner/repo N # 对某 issue 跑分诊(Sprint 2)
"""
import argparse
import asyncio

from maintainer_copilot.graph.supervisor import build_graph
from maintainer_copilot.models.llm import ModelProvider


async def run_chat() -> None:
    llm = ModelProvider()
    messages: list[dict] = []
    print("Maintainer Copilot CLI（输入 exit 退出）")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not text:
            continue
        if text.lower() == "exit":
            return
        messages.append({"role": "user", "content": text})
        result = await llm.chat(messages)
        tag = "(fallback)" if result.used_fallback else ""
        print(f"bot[{result.model}]{tag}> {result.content}")
        messages.append({"role": "assistant", "content": result.content})


async def run_ask(repo: str, question: str) -> None:
    """代码库问答: SolverWorker 全链路(查询改写 + 混合检索 + 带引用回答)。"""
    from maintainer_copilot.workers.solver import SolverWorker

    result = await SolverWorker().solve(question, repo)
    print(result["draft"])
    if result["citations"]:
        print("\n--- 引用 ---")
        for i, c in enumerate(result["citations"], 1):
            print(f"[{i}] {c['source']}:{c['path']}")


async def _load_issue(repo: str, issue: int) -> dict:
    """加载 issue 数据: 优先本地采集, 其次 GitHub API。"""
    import json as _json
    from pathlib import Path

    from maintainer_copilot.config import get_settings
    from maintainer_copilot.tools.github_client import GitHubClient

    path = Path("data") / "raw" / f"{repo.replace('/', '__')}-issues.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = _json.loads(line)
            if item["number"] == issue:
                print("(数据来源: 本地采集)")
                return item
    data = await GitHubClient(token=get_settings().github_token).get_issue(repo, issue)
    print("(数据来源: GitHub API)")
    return data


async def run_demo_triage(repo: str, issue: int) -> None:
    """对指定 issue 跑真实分诊(Worker 直调, 不经 HITL 闸门)。"""
    from maintainer_copilot.workers.triage import TriageWorker

    issue_data = await _load_issue(repo, issue)
    title = issue_data.get("title", "")
    body = issue_data.get("body", "") or ""
    result = await TriageWorker().triage(repo, title, body, issue)
    print(f"# 分诊结果 {repo}#{issue}: {title}")
    print(f"真实 labels: {[l['name'] for l in issue_data.get('labels', [])]}")
    print(f"分类: {result['category']} (置信度 {result['confidence']})")
    print(f"建议标签: {result['labels']}")
    print(f"相似历史 issue: {result['similar_issues']}")
    if result.get("reply"):
        print(f"回复草稿:\n{result['reply']}")
    else:
        print("回复草稿: (低置信度降级, 仅打标签)")


async def run_demo_hitl(repo: str, issue: int) -> None:
    """HITL 全链路演示: 分诊 -> Reflector 自审 -> 闸门挂起 -> 人工决策 -> Executor。"""
    import time

    from langgraph.types import Command

    from maintainer_copilot.graph.supervisor import build_graph

    issue_data = await _load_issue(repo, issue)
    state = {
        "repo": repo,
        "task_type": "triage",
        "issue": {
            "title": issue_data.get("title", ""),
            "body": issue_data.get("body", "") or "",
            "number": issue,
        },
        "messages": [{"role": "user", "content": f"分诊 {repo}#{issue}"}],
    }
    graph = build_graph()
    config = {"configurable": {"thread_id": f"demo-hitl-{repo}-{issue}-{int(time.time())}"}}
    payload = None
    async for event in graph.astream(state, config, stream_mode="updates"):
        inter = event.get("__interrupt__")
        if inter:
            first = inter[0] if isinstance(inter, (list, tuple)) else inter
            payload = first.value if hasattr(first, "value") else first
            break
    if payload is None:
        print("未到达 HITL 闸门(走降级路径, 无草稿可审)")
        return
    print("=" * 60)
    print(f"草稿待审 ({payload.get('task_type')}) 自审: {payload.get('reflection')}")
    print("-" * 60)
    print(payload.get("draft", ""))
    print("-" * 60)
    choice = input("决策 [a=批准 / e=编辑 / r=驳回]: ").strip().lower()
    decision: dict = {"decision": "rejected", "edited_draft": None}
    if choice == "a":
        decision["decision"] = "approved"
    elif choice == "e":
        decision = {"decision": "edited", "edited_draft": input("编辑后文本: ").strip()}
    final_action: dict = {}
    async for event in graph.astream(Command(resume=decision), config, stream_mode="updates"):
        if "executor" in event:
            final_action = event["executor"].get("final_action", {})
    print("Executor 结果:", final_action)


def main() -> None:
    parser = argparse.ArgumentParser(prog="mc", description="Maintainer Copilot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chat", help="交互式问答")
    sub.add_parser("serve", help="启动 Web 服务(审核台+webhook)")
    sub.add_parser("mcp", help="MCP stdio 模式启动工具服务")
    ask = sub.add_parser("ask", help="代码库问答(带引用)")
    ask.add_argument("--repo", required=True)
    ask.add_argument("question")
    demo = sub.add_parser("demo-triage", help="对指定 issue 跑分诊")
    demo.add_argument("repo")
    demo.add_argument("issue", type=int)
    hitl = sub.add_parser("demo-hitl", help="HITL 全链路演示(分诊->自审->人工决策->执行)")
    hitl.add_argument("repo")
    hitl.add_argument("issue", type=int)
    args = parser.parse_args()

    if args.cmd == "chat":
        asyncio.run(run_chat())
    elif args.cmd == "serve":
        import uvicorn

        from maintainer_copilot.config import get_settings

        s = get_settings()
        uvicorn.run("maintainer_copilot.server:app", host=s.host, port=s.port)
    elif args.cmd == "mcp":
        from maintainer_copilot.tools.mcp_server import build_mcp_server

        build_mcp_server().run(transport="stdio")
    elif args.cmd == "ask":
        asyncio.run(run_ask(args.repo, args.question))
    elif args.cmd == "demo-triage":
        asyncio.run(run_demo_triage(args.repo, args.issue))
    elif args.cmd == "demo-hitl":
        asyncio.run(run_demo_hitl(args.repo, args.issue))


if __name__ == "__main__":
    main()
