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


async def run_demo_triage(repo: str, issue: int) -> None:
    # TODO(Sprint 2): 真实分诊; 脚手架阶段验证状态机可编译
    graph = build_graph()
    state = {
        "repo": repo,
        "task_type": "triage",
        "messages": [{"role": "user", "content": f"请分诊 {repo}#{issue}"}],
    }
    print("状态机已编译, 节点:", list(graph.get_graph().nodes.keys()))
    print("提示: 完整 HITL 流程需 stream/resume, 在 D7 演示脚本中实现")


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


if __name__ == "__main__":
    main()
