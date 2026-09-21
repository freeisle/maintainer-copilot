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

## 5. 评测体系（1 分钟）

展示 `docs/eval-report/` 下的报告：
- 分诊基线：apache/dubbo 真实标签，macro-F1 0.49（bug 0.86）；
- 问答基线：LLM-as-judge 三维 rubric + 引用硬指标；
- 讲迭代故事：首轮 QA 低分 → 定位判官引用编号错配（评测框架自身 bug）→ 修复重跑，全程 commit 可追溯。

## 6. 收尾（1 分钟）

展示 commit 历史：Conventional Commits，从空仓库按 milestone 演进；`docs/architecture.md` 记录关键取舍（为什么不用 A2A/ES/Milvus）。

## 真实发布示例

[freeisle/ragent#2](https://github.com/freeisle/ragent/issues/2)：真实 webhook 分诊 → 自审通过 → 人工批准 → 发布评论 + 打标签（question/milvus/configuration）。
