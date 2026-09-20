"""LLM-as-Judge：三维 rubric + 双判官 + 位置交换。

D9 完善: 用更强模型做判官; 缓解偏差(位置交换/温度0/明确 rubric);
判官误判兜底 = 引用精确率程序硬指标 + 人工抽检 20%。
"""
import json

JUDGE_PROMPT = """你是评审专家。请按 rubric 对回答打分(0-5 整数), 只输出 JSON:
{{"correctness": int, "citation_support": int, "usefulness": int, "reason": str}}

rubric:
- correctness: 回答与标准答案要点一致, 无事实错误
- citation_support: 每条引用片段都支撑对应结论, 无无关引用
- usefulness: 对提问者有实际帮助(可直接采纳)

标准答案: {reference}
待评回答: {candidate}
"""


JUDGE_PROMPT_NO_REF = """你是评审专家。请按 rubric 对回答打分(0-5 整数), 只输出 JSON:
{{"correctness": int, "citation_support": int, "usefulness": int, "reason": str}}

rubric:
- correctness: 回答与给定引用资料一致, 无编造、无引用之外的具体事实
- citation_support: 具体结论都标注了 [n] 且对应引用确实支撑该结论
- usefulness: 对提问者有实际帮助(可直接采纳)

引用资料:
{citations}

问题: {question}
待评回答: {candidate}
"""


class Judge:
    def __init__(self, llm) -> None:
        self.llm = llm

    async def score(self, reference: str, candidate: str) -> dict:
        # TODO(D9+): 双判官 + 位置交换(两次调用互换顺序取均值)
        result = await self.llm.chat(
            [
                {
                    "role": "user",
                    "content": JUDGE_PROMPT.format(reference=reference, candidate=candidate),
                }
            ],
            temperature=0.0,
        )
        try:
            return json.loads(result.content)
        except json.JSONDecodeError:
            return {"error": "judge 输出不可解析", "raw": result.content}

    async def score_no_ref(self, citations: list[dict], question: str, candidate: str) -> dict:
        """无标准答案模式: 依据引用资料本身评估(与硬指标互相校验)。"""
        cite_text = "\n".join(
            f"[{i}] ({c.get('source', '')}:{c.get('path', '')}) {c.get('snippet', '')[:150]}"
            for i, c in enumerate(citations, 1)
        ) or "(无引用)"
        result = await self.llm.chat(
            [
                {
                    "role": "user",
                    "content": JUDGE_PROMPT_NO_REF.format(
                        citations=cite_text, question=question, candidate=candidate
                    ),
                }
            ],
            temperature=0.0,
        )
        try:
            return json.loads(result.content)
        except json.JSONDecodeError:
            return {"error": "judge 输出不可解析", "raw": result.content}
