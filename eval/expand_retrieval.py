"""Build the versioned extension from inspected source locations, never rankings."""
import json
from pathlib import Path

DATA = Path(__file__).parent / "datasets"
RAG = "rag/src/main/java/com/nageoffer/ai/ragent/"
MC = "src/maintainer_copilot/"
CASES = [
    ("freeisle/ragent", "symbol", "VectorTargetResolver 如何确定 embedding model 和 dimension？", [RAG + "knowledge/support/VectorTargetResolver.java"], "VectorTarget resolve"),
    ("freeisle/ragent", "error", "报错：知识库未配置嵌入模型，在哪校验？", [RAG + "knowledge/support/VectorTargetResolver.java"], "知识库未配置嵌入模型"),
    ("freeisle/ragent", "error", "流水线存在多个起始节点怎么处理？", [RAG + "ingestion/engine/IngestionEngine.java"], "流水线存在多个起始节点"),
    ("freeisle/ragent", "english", "How does vector retrieval filter multiple collection names?", [RAG + "rag/core/vector/MilvusVectorRetrieverService.java"], "buildCollectionFilter"),
    ("freeisle/ragent", "multi_source", "向量检索的接口及 Milvus 实现在哪里？", [RAG + "rag/core/vector/VectorRetrieverService.java", RAG + "rag/core/vector/MilvusVectorRetrieverService.java"], "VectorRetrieverService"),
    ("freeisle/12306", "error", "chooseSeats 传空字符串为什么抛 StringIndexOutOfBoundsException？", ["ai-agent-py/app/tools/order_tools.py"], "StringIndexOutOfBoundsException"),
    ("freeisle/12306", "symbol", "build_idempotency_key 如何防止重复下单？", ["ai-agent-py/app/tools/order_tools.py"], "build_idempotency_key"),
    ("freeisle/12306", "english", "Is waitlist success a trained model or a heuristic?", ["ai-agent-py/app/planning/waitlist_scorer.py"], "启发式"),
    ("freeisle/12306", "error", "登录响应缺少 accessToken 时在哪里报错？", ["ai-agent-py/app/tools/client.py"], "登录响应缺少 accessToken"),
    ("freeisle/12306", "configuration", "Authorization 头需要 Bearer 前缀吗？", ["ai-agent-py/app/tools/client.py"], "无 Bearer 前缀"),
    ("local/maintainer-copilot", "symbol", "GitHubClient 的 _cache_key 为什么包含 headers？", [MC + "tools/github_client.py"], "headers 也必须入键"),
    ("local/maintainer-copilot", "multi_source", "HumanGate 审批后哪一个节点可以发布评论？", [MC + "graph/supervisor.py", MC + "graph/executor.py"], "executor"),
    ("local/maintainer-copilot", "configuration", "MC_DRY_RUN 默认是否阻止 GitHub 真实写入？", [MC + "config.py", MC + "graph/executor.py"], "dry_run"),
    ("local/maintainer-copilot", "english", "How are exact error strings extracted for retrieval?", [MC + "rag/query_rewriter.py"], "extract_error_strings"),
    ("local/maintainer-copilot", "hard_negative", "没有依据时 Solver 会编造回答还是拒绝？", [MC + "workers/solver.py"], "REFUSAL_TEXT"),
    ("local/maintainer-copilot", "hard_negative", "同一 GitHub delivery 重放为什么不会重复分诊？", [MC + "server.py"], "mark_delivery"),
]


def build():
    baseline = [json.loads(x) for x in (DATA / "retrieval.jsonl").read_text(encoding="utf-8").splitlines()]
    extra = [dict(id=f"ret-{i:03}", repo=repo, query_type=kind, question=q,
                  relevant=paths, evidence=needle,
                  note="源码人工核对；evidence 为标注文件中可核验字符串，不由检索排名生成")
             for i, (repo, kind, q, paths, needle) in enumerate(CASES, 9)]
    (DATA / "retrieval-v2.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in baseline + extra), encoding="utf-8")


if __name__ == "__main__":
    build()
