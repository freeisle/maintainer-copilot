# FAQ / 模板反馈闭环

Executor 在人工决定后保存完整原稿、修订稿、问题、引用及任务类型到 adoption.sqlite 的 skill_feedback 表。turn_id 对 checkpoint 重放去重；旧的 300 字 note 保留兼容，但不足以还原历史完整反馈，不自动作为训练标签。

每周由部署调度器运行（同周同仓库幂等；无反馈明确失败）：

```bash
python -m scripts.skill_evolution generate --repo owner/name
python -m scripts.skill_evolution pending
python -m eval.skill_regression --id CANDIDATE --output data/skill-regression.json
python -m scripts.skill_evolution approve --id CANDIDATE --reviewer maintainer --note "核验反馈与回归报告"
# 或 reject；生成步骤没有 approve 权限，也不会写 skill 包
python -m scripts.skill_evolution history --repo owner/name
python -m scripts.skill_evolution rollback --repo owner/name --version 0 --reviewer maintainer
```

CLI 是本机维护者审核入口，不暴露给 LLM 工具。候选、反馈依据 ID、审核人、说明、历史版本均保留；回滚追加新版本，version=0 禁用增量 FAQ。只有已审核版本进入生产 skill_context。过期 base_version 的候选不能直接覆盖新版本。

本地验证：完整反馈→候选→审核前不生效→批准后加载→回滚、驳回不生效、跨仓库隔离、原 Solver 引用规则回归均通过。真实维护者反馈和候选语义回归尚未完成，不能声称 FAQ 质量已提升；当前不自动生成或批准生产模板。
