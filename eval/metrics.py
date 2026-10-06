"""评测指标(纯函数, 可单测)：分类 macro-F1 / 检索命中率 / 引用精确率。"""


def macro_f1(
    y_true: list[str], y_pred: list[str], labels: list[str] | None = None
) -> dict:
    labels = labels or sorted(set(y_true) | set(y_pred))
    per: dict[str, dict] = {}
    for lab in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p != lab)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per[lab] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    macro = sum(v["f1"] for v in per.values()) / len(per) if per else 0.0
    return {"macro_f1": macro, "per_class": per}


def retrieval_hit_at_k(relevant: set[str], retrieved: list[str], k: int = 8) -> float:
    """命中率: 前 k 个结果中是否包含任一相关文档。"""
    return 1.0 if relevant & set(retrieved[:k]) else 0.0


def mrr_at_k(relevant: set[str], retrieved: list[str], k: int = 8) -> float:
    """MRR@k: 第一个相关文档的倒数排名; 前 k 无命中返回 0。

    与 hit@k 互补: hit 只看有没有, MRR 看排得靠不靠前, 用于区分排序质量。
    """
    for rank, key in enumerate(retrieved[:k], start=1):
        if key in relevant:
            return 1.0 / rank
    return 0.0


def recall_at_k(relevant: set[str], retrieved: list[str], k: int = 8) -> float:
    """Recall@k: 前 k 个结果覆盖了多大比例的相关文档; 无标注返回 0。

    多标注问句(如"有哪些节点")主要看这个, 单标注问句与 hit@k 等价。
    """
    if not relevant:
        return 0.0
    return len(relevant & set(retrieved[:k])) / len(relevant)


def doc_key(doc: dict) -> str:
    """检索结果的稳定文档键, 与 eval/datasets/retrieval.jsonl 的 relevant 标注口径一致。

    - issue chunk: issue#{编号}(issue chunk 无 path 元数据)
    - 代码/文档 chunk: 仓库相对路径(ingest 时已统一为正斜杠)
    """
    if doc.get("source") == "issue":
        meta = doc.get("meta") or {}
        number = meta.get("issue")
        if number not in (None, ""):
            return f"issue#{number}"
    return str(doc.get("path") or doc.get("id") or "")


def citation_precision(cited_texts: list[str], answer: str, min_len: int = 6) -> float:
    """引用精确率(程序硬指标): 引用片段是否被回答实际覆盖。

    程序硬指标: 6 字符子串 overlap(无模型参与, 与 LLM 判官互相校验)。
    """
    if not cited_texts:
        return 0.0
    supported = 0
    for text in cited_texts:
        if _overlap(text, answer, min_len):
            supported += 1
    return supported / len(cited_texts)


def _overlap(cited: str, answer: str, min_len: int) -> bool:
    for i in range(len(cited) - min_len + 1):
        if cited[i : i + min_len] in answer:
            return True
    return False
