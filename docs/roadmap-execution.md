# Roadmap 执行记录

起点：`3f1006c`，工作区干净；分支 `feat/roadmap-implementation`。不修改历史 baseline 或原始标注。

|顺序 / 任务|现状与代码位置|补齐与验收|依赖 / 风险|
|---|---|---|---|
|1 重复入库|scripts/ingest.py 按扫描目录计算相对路径|统一仓库根路径；重复预览、事务备份和恢复；回归测试|旧库迁移必须显式执行|
|2 评测扩充|eval/datasets/retrieval.jsonl 8 条|增加仓库、查询类型和困难样本；标签来源核验；保留旧集|本地源代码；不可修改标签迎合排序|
|3 召回优化|rag/retriever.py 三通道|标识符分词、路径信号、去重与覆盖；同集对比 Hit/Recall/MRR|先固定评测；不承诺指标提升|
|4 checkpoint|graph/supervisor.py 错用同步 saver 工厂|异步兼容持久化、跨进程 interrupt/resume、失败显式报告|单机 SQLite；写执行仍可能有外部幂等边界|
|5 短期记忆|memory/short_term.py 未接线|图和 CLI 多轮裁剪、摘要、锚点、隔离验证|checkpoint；避免 add_messages 重复追加|
|6 429 降级|GitHubClient 快速失败|只在限流时使用本地证据；空库拒答、错误分类与日志|检索；不能伪造 PR diff|
|7 Skill 自迭代|决策记录只保存 300 字 note|完整反馈、周级候选、人工审核、版本回滚、生产加载|持久化与记忆；审核前不可生效|
|8 联邦检索|单 repo 检索|显式允许列表、来源标记、隔离与失败处理、Solver 接线|召回；跨库资料不隐式混入|
|9 A2A|单进程 Worker|独立只读知识服务、发现、请求响应、超时错误、实际端到端|联邦检索；不把自定义 RPC 冒称标准协议|
|10 Java MCP|未实现|Spring Boot 服务 + Python MCP 客户端实际调用|Java/Maven、依赖下载|
|11 Langfuse|未实现|自托管配置、trace 与评测接线、实际上报|Docker 当前未发现；外部运行验证可能阻塞|
|12 后训练|弱标注与 HITL 数据量待检查|收集、质量门槛、去泄漏划分、训练和基线对比流程|数据不足不声称已训练或达到收益|

执行环境：使用 `.venv/Scripts/python.exe`；pytest 指定 `--basetemp=data/test-tmp-<阶段>`。默认 uv 缓存与 pytest 系统临时目录受沙箱权限限制。现有 PostgreSQL 只读连接探测超时；不输出连接凭据。

每阶段记录测试和提交，只有全部验收条件满足才勾选 roadmap。

## 阶段 1：双路径来源修复

`scripts/ingest.py` 从 Git 根计算文档和代码路径，扫描 `docs/` 与全仓库产生相同 chunk 身份。
`python -m scripts.dedup_index --repo owner/name` 默认仅预览；仅全文、标题、分段和仓库都相同的 `docs/` 别名可清理。
显式 `--apply` 在同一事务内保存完整行（包括向量）到 `chunk_alias_backups` 后删除别名；`--restore <batch>` 恢复且不覆盖更新后的行。
执行前备份数据库；执行后重启检索进程以清空 BM25 缓存。本轮未执行现有数据删除。

验证：`.venv/Scripts/python.exe -m pytest -q --basetemp=data/test-tmp-verified` → **111 passed**（包含 109 项既有测试）。
Windows 沙箱在仓库内创建的 pytest 私有目录同样遇到 ACL 错误；正常权限执行通过。PostgreSQL 在沙箱外重试仍超时，因此迁移实库验收未完成。

阶段 1 提交：`656ff5f`。

## 阶段 2：独立 v2 评测集

`retrieval-v2.jsonl` 共 24 条，前 8 条与原集完全相同，新增 16 条覆盖符号、错误串、英文、配置、多来源及困难负例语境，增加当前项目作为第三个仓库。
所有新增标注逐文件核验 `evidence` 字符串；来源为当前源码，不从排序结果倒推标签。`python -m eval.expand_retrieval` 可重建扩展集。
`python -m eval.run_eval --suite retrieval --dataset retrieval-v2` 接入原评测入口；默认仍使用旧集。
验证：`pytest -q tests/test_retrieval_dataset.py` → **1 passed**；16 个新增案例的所有标注源码验证通过。
新仓库需要先按 `local/maintainer-copilot` 键入库，线上混合指标仍受 PostgreSQL 不可达阻塞。

阶段 2 提交：`384a302`。

## 阶段 3：词法召回与来源覆盖

复用 `LexicalIndex` 到线上 PostgreSQL BM25 与可独立运行的 `LocalRetriever`。保留完整标识符并补充 camelCase/snake_case 分词，加入路径信号。
RRF 单通道内重复 ID 不再重复加分；重排后优先覆盖不同文档，保留后续同文件 chunk。旧 docs 别名按内容证据过滤，读取 SQL 强制 repo 范围。

验证：`pytest -q tests/test_lexical.py tests/test_rrf.py tests/test_solver.py tests/test_dedup.py` → **16 passed**。
`python -m eval.offline_retrieval --build` → n=24、同一源码快照旧/新词法对照：Hit@3 **0.5000→0.6250**，Recall@5 **0.5407→0.5903**，MRR@8 **0.4379→0.5486**。
逐项排名、快照和标签 SHA256 见 `eval/reports/retrieval-offline-v2.json`。后续复跑去掉 `--build` 固定同一语料；快照存于忽略的 data 目录。
这不是历史混合检索 baseline 的提升：未运行 embedding、query rewrite、reranker；困难语义召回仍有失败，不能用词法收益代替完整线上验证。因此任务保留未完成。
