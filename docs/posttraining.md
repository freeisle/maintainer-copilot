# 后训练：先验证数据，再训练

本轮真实数据审查：`data/adoption.sqlite` 只有 2 条旧版 hitl_decisions，没有完整 skill_feedback；不能从被截断的 note 还原正确监督目标。环境 PyTorch 2.14.0+cpu / Transformers 5.17.0，CUDA 不可用。未训练或替换业务模型。

Executor 新增的完整反馈是数据入口。导出只读数据库，只接受 approved 原稿或 edited 最终稿，驳回稿永不作为答案。保留反馈 ID、repo、问题组；检查空文本、重复、长度、疑似凭据、可重建的引用，排除既有 eval 查询。旧反馈缺完整引用上下文时保守剔除。简单凭据扫描不能替代人工数据审查。

```powershell
uv sync --extra rag
uv run python scripts/posttrain.py prepare --output data/training/v1
# 审查 manifest.json、train/validation/test.jsonl 后，数据门槛满足才运行：
uv run python scripts/posttrain.py train --dataset data/training/v1 --model models_cache/local-causal-model --output data/training/candidate-v1 --epochs 1
```

按问题/issue 组的稳定 hash 划分 80/10/10；至少 40/5/5 样本只是工程最低门槛，不代表充分数据量。相同 issue 的修订不得跨集；数据校验 SHA256、源组交集，拒绝版本覆盖。固定评测集不参与训练或阈值调参。

训练使用本地 AutoModelForCausalLM 与 tokenizer（禁止自动下载及 remote code），标准监督 token loss，prompt labels=-100，禁止静默截断丢失答案，固定 seed=42，梯度裁剪、非有限 loss 拒绝。当前为单机 CPU 小模型全参数训练，内存需足够；较大模型的 LoRA/GPU 方案未实现。

输出独立 safetensors 模型、数据 hash、训练/验证曲线、训练前后相同 test 集 NLL、引用标记格式、token-F1、单样本生成耗时和原始预测。保留真实退化结果，不自动部署。NLL/表面格式/token-F1 不代表知识正确性；部署前仍须在固定 QA/triage 集上运行现有评测与人工审阅，并检查延迟/成本是否改善。通过配置显式切换推理服务，回滚恢复原 primary_model/base_url；训练命令不会修改它们。

`tests/test_training.py` 构建随机初始化的微型 GPT-2 与合成反馈，实际训练、保存、重载并验证权重改变、mask、数据门槛和防篡改。这仅验证训练工程链路，不是专精能力或收益证据。真实数据不足时 roadmap 保持未完成。
