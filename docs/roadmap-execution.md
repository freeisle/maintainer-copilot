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

阶段 3 提交：`1741a27`。

## 阶段 4：持久化 checkpoint

修复原来把 context manager 当 saver、且同步 saver 不支持 astream 的问题。新增 PersistentSaver 复用上游存储并桥接异步调用，适配现有两个事件循环。依赖及 lock 已更新；保留原依赖镜像地址，避免无关锁文件重写。
审批 API 校验 decision、拒绝空编辑及已处理项重复请求。
验证：`pytest -q tests/test_checkpoint_persistence.py tests/test_graph.py tests/test_webhook.py` → **9 passed**。三个独立进程重启场景分别验证 approved/edited/rejected。详见 `docs/persistence.md`。

阶段 4 提交：`6457dce`。

## 阶段 5：短期记忆接线

图新增 memory 入口，使用 LangGraph RemoveMessage 替换历史而非把裁剪结果再次追加；同一 checkpoint 存储摘要，不新增记忆数据库。Solver 将历史传给回答模型，Executor 将审核后的回复加入历史。
新轮次清除旧审批、改写计数、执行结果，防止上轮批准状态泄漏；同一 thread_id 不允许切换仓库。
`MC_MEMORY_MAX_TOKENS=4000` 控制图预算；CLI 同样启用严格裁剪。滚动摘要只保留一个前缀并限制长度；极长单条消息会截短，普通任务锚点保持原文。
首次测试发现旧 trim 契约要求完整保留最近消息；新增 `strict_budget` 可选参数保持旧调用兼容，生产入口显式启用。
验证：`pytest -q tests/test_memory.py tests/test_workflow_memory.py tests/test_solver.py tests/test_executor.py tests/test_adoption.py` 修复后 **28 passed**；补充 Solver 历史进入 LLM 请求的专项测试。

阶段 5 提交：`a7f5be2`；Solver 专项 **9 passed**。

## 阶段 6：429 到本地索引

设置 `MC_LOCAL_INDEX_PATH` 指向 JSONL 快照（可用阶段 3 的构建命令生成）。MCP 三个读工具仅在 429 或明确限流的 403 下返回带 degraded/stale/source 的证据，不伪装实时 API 结果；文件路径和 issue 编号使用精确匹配。
PR 初审遇到限流时返回本地引用和“无法获得当前 diff，暂不能作初审结论”，不把历史代码冒充当前 PR。
GitHub 工具外层 retries=0，5xx/网络最多仍为客户端的 4 次；401/普通403、超时分别返回 auth/timeout/service，不触发索引降级。
验证：`pytest -q tests/test_rate_fallback.py tests/test_github_client.py tests/test_mcp.py tests/test_reviewer.py` → **21 passed**；补充 PR 实际 HTTP→快照证据测试通过（修正测试初版错误 Accept 头）。

阶段 6 提交：`d07fb0e`。

## 阶段 7：Skill 反馈与审核

原 adoption note 仅 300 字，新增完整反馈表（不改旧表）、周级候选、审核记录与追加版本历史；候选不自动生效，版本冲突拒绝覆盖。生产 skill_context 读取审核后的 FAQ overlay，回滚无需修改代码文件。
`scripts/skill_evolution.py` 提供本机生成/查看/审核/回滚命令；`eval/skill_regression.py` 在不激活候选的前提下比较真实 Solver QA 输出和引用硬指标。
验证：`pytest -q tests/test_evolution.py tests/test_skills.py tests/test_adoption.py tests/test_solver.py` → **22 passed**。这验证工程闭环，不替代真实候选的语义质量评测；后者依赖真实反馈与检索后端，任务保留未完成。详见 `docs/skill-evolution.md`。

阶段 7 提交：`ecfa09f`。

## 阶段 8：显式联邦检索

`FederatedRetriever` 仅按维护者配置扩展仓库，限定并发/超时，按源内排名融合，给 ID 和引用附上 repo；本地快照与混合检索均可复用。Solver 默认工厂接入配置。
验证：`pytest -q tests/test_federated.py tests/test_solver.py` → **11 passed**，覆盖双仓库真实快照进入回答、无授权仓库隔离、部分失败和全部失败。配置见 `docs/services.md`。

阶段 8 提交：`c19dddb`。

## 阶段 9：A2A 可选服务边界

具体场景为多个维护工作流复用一个只读知识进程；Supervisor/HITL/Executor 仍在原进程。服务协议固定 A2A 0.3，客户端通过配置接入 Solver。
`pytest -q tests/test_a2a.py tests/test_federated.py` → **4 passed**，包含 uvicorn 独立进程、实际 TCP HTTP 发现/请求响应；补充超时及 completed task get/cancel 契约测试。未开启公网服务，无自动写动作。


阶段 9 提交：`cb52aac`。

## 阶段 10：Java MCP 互操作

Spring Boot 3.5.7 / Spring AI 1.1.0 工具服务提供只读 `inspect_pom`，现有 FastMCP 客户端调用。首次真实测试定位到协议握手/环境代理兼容问题，修复后运行 `pytest tests/test_java_mcp.py -q` → **1 passed**（独立 Java 进程、真实 HTTP）；`mvn -B -f services/java-tools/pom.xml test package` → **BUILD SUCCESS, 3 tests passed**。CI 加入 Java 构建，部署/使用见 services.md。


阶段 10 提交：`02df70d`。

## 阶段 11：观测与部署准备

默认 opt-in、无内容的 Solver/LLM 父子 OTLP trace，评测入口关联数值 score，本地审计落盘，网络失败/partial rejection 脱敏日志、不重试。生成官方 v4.63.0 校验过的独立 Compose 与随机凭据（仅 ignored data），不覆盖或触碰原 RAG 库。实际执行 prepare 成功。
`pytest tests/test_observability.py tests/test_llm.py tests/test_solver.py -q` → **14 passed**，真实本地 HTTP 接收 + 本地文件验证。无 Docker，无法验证真实 Langfuse UI/存储和基础设施镜像 digest，因此不勾选完成。
