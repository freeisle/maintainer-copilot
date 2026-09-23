# 面试演示脚本（约 10 分钟）

> 演示前提：VM 数据库已启动、`uv sync` 完成、`.env` 配置好。
> 首次运行需下载模型（bge-m3 ~2.3GB + reranker ~1.1GB，走 hf-mirror），建议提前跑一次 `mc ask` 预热。

## 1. 开场（30 秒）

一句话定位 + README 架构图：

> 这是我从 0 到 1 做的开源维护者 AI 协作者：issue 自动分诊、代码库问答、HITL 人工把关。写工具（评论/标签）不进 LLM 的工具列表，所有对外输出必须经人工批准——结构性杜绝幻觉直接发布。

## 2. 代码库问答（2 分钟）

```bash
uv run mc ask --repo freeisle/12306 "候补购票是怎么实现的？"
uv run mc ask --repo freeisle/ragent "Milvus 数据源怎么配置？"
```

要点：答案带 [n] 引用；可指出「依据不足」时明确拒绝回答，不编造。

## 3. 分诊演示（2 分钟）

```bash
uv run mc demo-triage freeisle/ragent 108
```

要点：四分类（bug/feature/question/invalid）+ 标签建议 + 回复草稿；相似历史 issue 的 label 作弱标注信号。

## 4. HITL 闭环（3 分钟，核心）

```bash
uv run mc demo-hitl freeisle/ragent 67
# 输入 a 批准 / e 编辑 / r 驳回
```

要点：
- Reflector 自审三查（有出处/答所问/符合规范），驳回时带修改建议重写（≤2 次后降级）；
- 现场演示「自审抓出草稿编造事实」案例（曾编造"用户已覆盖 4 个测试用例"被拦截）；
- Executor 在批准后执行；默认 dry-run，`MC_DRY_RUN=false` 时真实发布。

## 5. PR 初审（2 分钟，可选加场）

```bash
# 在 fork 上准备一个测试 PR, 然后:
uv run mc demo-review freeisle/ragent 3
# 输入 a 批准 / e 编辑 / r 驳回
```

要点（真实演示记录，见 commit 2a9cfcb / e77323b / 842eb03）：
- 全链路：拉取 PR diff → 解析改动文件 → 混合检索相关代码/文档 → 四维初审（正确性/规范/测试覆盖/风险）→ Reflector 自审 → 闸门 → Executor；
- 初审意见带 [n] 引用，并诚实标注「与改动无直接关联的资料未作为审查依据」；纯文档改动会明确跳过正确性/测试维度（不硬凑）；
- 两个真实排障故事：① `pr` 键不在 AgentState schema 被 LangGraph 静默丢弃 → 节点恒拒（E2E 抓出，加 schema 回归测试）；② 自审视野（150 字符）小于草稿视野（500 字符）→ 草稿引用自审看不到、连续误杀 → 对齐视野后一次通过；
- diff 过大自动截断进入「只总结」模式，不逐行判错。

## 6. 评测体系（1 分钟）

展示 `docs/eval-report/` 下的报告：
- 分诊基线：dubbo+rocketmq 真实标签弱标注 77 条 4 类，macro-F1 0.49（bug 0.79 / feature 0.50 / question 0.68）；27 例错误三层根因已在报告分析（判别规则反作用 / invalid 标注口径冲突 / 带日志的 enhancement 硬案例），下一轮方向明确；
- 问答基线：LLM-as-judge 三维 rubric，收紧引用约束后 correctness 3.6 / citation_support 3.6（初版 2.7/2.2）；
- 工具选择：25 条真实查询准确率 1.00，模糊描述消融 0.92（描述质量贡献可量化）；
- 讲迭代故事：首轮 QA 低分 → 定位判官引用编号错配（评测框架自身 bug）→ 修复重跑 → Prompt 收紧再迭代，全程 commit 可追溯。

## 7. 长期记忆与采纳率（30 秒，可选）

```bash
uv run mc prefs set freeisle/12306 reply_language en   # 冲突时挂起待裁决
uv run mc prefs resolve freeisle/12306 reply_language new
uv run mc adoption                                    # HITL 草稿采纳率(真实操作累计)
```

## 8. 收尾（1 分钟）

展示 commit 历史：Conventional Commits，从空仓库按 milestone 演进；`docs/architecture.md` 记录关键取舍（为什么不用 A2A/ES/Milvus）。

## 真实发布示例

[freeisle/ragent#2](https://github.com/freeisle/ragent/issues/2)：真实 webhook 分诊 → 自审通过 → 人工批准 → 发布评论 + 打标签（question/milvus/configuration）。

PR 初审 E2E：[freeisle/ragent#3](https://github.com/freeisle/ragent/pull/3)（演示后已关闭）：拉 diff → 初审 → 自审两轮排障后通过 → 批准 → dry-run 记录评论动作。
