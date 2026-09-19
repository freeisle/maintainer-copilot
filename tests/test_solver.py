"""SolverWorker 纯函数单测：引用解析 / 多轮检索合并。"""
from maintainer_copilot.workers.solver import merge_results, parse_citations


def test_parse_citations_dedup_and_order() -> None:
    assert parse_citations("结论一 [1] 结论二 [2][1] 结论三 [3]") == [1, 2, 3]
    assert parse_citations("没有引用的回答") == []


def test_parse_citations_ignores_invalid() -> None:
    # 解析层只取数字; 编号有效性由 solve() 校验
    assert parse_citations("[99]") == [99]


def test_merge_results_dedupe_keep_max_score() -> None:
    batches = [
        [{"id": "a", "rerank_score": 0.8}, {"id": "b", "rerank_score": 0.6}],
        [{"id": "a", "rerank_score": 0.9}, {"id": "c", "rerank_score": 0.5}],
    ]
    merged = merge_results(batches)
    assert [m["id"] for m in merged] == ["a", "b", "c"]
    assert merged[0]["rerank_score"] == 0.9  # a 保留最高分
    assert len(merged) == 3  # 按 id 去重
