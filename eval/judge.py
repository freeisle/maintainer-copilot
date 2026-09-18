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


class Judge:
    def __init__(self, llm) -> None:
        self.llm = llm

    async def score(self, reference: str, candidate: str) -> dict:
        # TODO(D9): 双判官 + 位置交换(两次调用互换顺序取均值)
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
