"""混合检索演示 CLI。

用法:
  uv run python scripts/search.py --repo freeisle/12306 "候补购票失败"
"""
import argparse
import asyncio
import logging

from maintainer_copilot.rag.retriever import HybridRetriever

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def main() -> None:
    parser = argparse.ArgumentParser(description="混合检索演示")
    parser.add_argument("query")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    retriever = HybridRetriever()
    results = await retriever.retrieve(args.query, args.repo, top_k=args.top_k)
    print(f"查询「{args.query}」({args.repo}) 命中 {len(results)} 条:")
    for i, r in enumerate(results, 1):
        score = r.get("rerank_score", 0.0)
        print(f"{i}. [{r['source']}] {r['path']} (score={score:.4f})")
        print(f"   {r['text'][:120].replace(chr(10), ' ')}")
    await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(main())
