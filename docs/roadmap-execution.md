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


阶段 11 提交：`58d574c`。

## 阶段 12：后训练数据门槛与可运行训练流程

只读审查发现旧 adoption 仅 2 条统计，没有完整反馈表；真实导出 train/validation/test 均为 0，ready=false，未改变业务模型。实现 approved/edited 目标采集、拒绝稿排除、凭据/长度/引用校验、问题分组隔离、数据 hash；训练本地 causal LM（prompt mask、梯度裁剪、seed）、独立模型落盘、相同 test 的训练前后 NLL/格式/token-F1/耗时对照。
首轮合成训练测试因验证集只有 2 条被门槛正确拦截；增加合成测试源组后，实际 tiny GPT-2 CPU 训练/保存/重载与权重变化验证通过，未降低真实数据门槛。
`pytest tests/test_training.py tests/test_llm.py tests/test_solver.py tests/test_skills.py tests/test_observability.py tests/test_mcp.py -q` → **32 passed**（其中训练 3 项）。这不是业务模型质量或收益结论。详见 posttraining.md 与 eval/reports/training-readiness.json。


阶段 12 提交：`a2b5473`；训练专项最终 **3 passed**。

## 收尾复查与全量回归

修复实际调用链中的边界：Solver 保留联邦排名，极小语料 BM25 非正 IDF 不丢弃真实匹配；禁用 Skill 同时禁用 overlay，候选回归按真实替换而非追加旧版本；新轮清除旧写目标；同一待审项并发审批返回 409，避免同进程重复执行。MCP 返回 error_kind；LLM 仅连接/超时/429/5xx 降级，鉴权/参数错误立即暴露，SDK 不隐式重试；无主 key 直接使用本地通道。
全量 `pytest -q --basetemp=data/test-tmp-full-roadmap` → **151 passed, 1 dependency deprecation warning**；最后补充缺配置、小语料及参数校验后的专项分别 **38 passed** 和 **9 passed**。`compileall -q src scripts eval tests` 通过。
`python -m eval.offline_retrieval --report data/retrieval-final.json` 再次复评同一 6349-chunk 快照，完整 JSON 与已提交报告相等，全部标签覆盖；不是重建语料后的不可比数字。


收尾修复提交：`5ec0870`。

## 交付状态与解除阻塞条件

| 任务 | 状态 | 功能阶段 commit | 验证 / 剩余条件 |
|---|---|---|---|
| 减少重复入库 | 部分完成，阻塞 | 656ff5f | 路径/别名测试通过；原 PostgreSQL 恢复后验证备份、迁移和回滚 |
| 检索评测扩充 | 完成 | 384a302 | 24 条 / 3 仓库；原 8 条不变，新增证据逐项核验 |
| 召回覆盖优化 | 部分完成，阻塞 | 1741a27 | 离线对照可复现；需原混合检索实库回归 |
| 持久化 checkpoint | 完成（单机边界） | 6457dce | 真实 LangGraph 跨进程批准/编辑/驳回恢复 |
| 短期记忆 | 完成 | a7f5be2 | 多轮、预算、摘要、锚点、线程隔离与旧写目标清理 |
| 限流降级 | 完成 | d07fb0e | 限流专属快照降级、无证据拒答、分类错误与重试边界 |
| Skill 自迭代 | 部分完成，阻塞 | ecfa09f | 工程闭环通过；需真实完整反馈候选和语义回归审核 |
| 联邦检索 | 完成 | c19dddb | 真实双仓库快照 + 隔离/部分失败/全部失败 |
| A2A | 完成（同步只读子集） | cb52aac | 独立服务进程真实 TCP 发现/调用，协议/超时/认证错误 |
| Java MCP | 完成 | 02df70d | Spring Boot 构建与 3 Java 测试；Python→Java 真实 MCP 测试 |
| Langfuse | 部分完成，阻塞 | 58d574c | 实际本地 HTTP OTLP/scores 验证；需 Docker、完整 Langfuse UI/存储验证 |
| 后训练 | 部分完成，阻塞 | a2b5473 | 实际合成 tiny 模型训练链路通过；真实可训练样本为 0 |

7 项完成、5 项保留 `[ ]`。未完成项均已实现可在当前环境验证的部分，没有无人处理的独立规划项。测试中的外部 LLM/Worker 替身只用于流程隔离；不作为真实答案质量提升证据。

没有执行历史索引清理、生产发布、远程 push 或业务模型替换；原工作区干净，无用户未提交改动需要避让。运行数据、快照、随机部署凭据、测试模型、Maven 缓存和 jar 保留在 Git 忽略目录，不进入提交。

后续依赖恢复后的顺序：先备份并验证历史索引迁移/回滚，再运行原混合检索 baseline/v2；收集完整人工反馈后做候选语义回归和训练数据审查；Docker 可用后启动独立 Langfuse 并验证真实面板。GitHub 写操作与 checkpoint 间的崩溃窗口仍不保证 exactly-once（见 persistence.md），无需为本轮只读/演练验证打开真实写权限。


## 补充：独立 PostgreSQL 事务验证

收尾环境复核发现 PATH 中有 PostgreSQL 16 原生工具。新建 `data/pg-migration-verification`，仅监听 127.0.0.1:15439，在测试创建的随机 schema 中执行迁移脚本，未连接或修改原配置数据库。测试结束停止该专用实例，保留 ignored 数据目录。
`MC_TEST_DATABASE_URL=postgresql://roadmap_test@127.0.0.1:15439/postgres` 下运行 `pytest tests/test_dedup_postgres.py -q --basetemp=data/test-tmp-postgres-migration` → **3 passed**。覆盖预览、完整行元数据/数值数组备份恢复、跨仓库隔离、恢复幂等、不覆盖更新、删除抛错回滚（连备份表创建也回滚）。
本机未安装 pgvector，测试 embedding 为 double precision[]；因此不冒充 vector(1024) 实际索引迁移验证。原历史清理仍未执行、roadmap 保留 `[ ]`。

最终完整回归 `pytest -q --basetemp=data/test-tmp-final-acceptance` → **154 passed, 1 warning**（anyio/Starlette 依赖弃用，无失败/跳过）；其后新增的 3 项 PostgreSQL 测试已单独实际执行通过。默认不提供 MC_TEST_DATABASE_URL 时这 3 项明确 skip，不连接生产数据库。

PostgreSQL 补充验证提交：`2d15232`。

## 2026-10-25：PostgreSQL / pgvector 实库验证

起点 `d693063`，本地/远端 `feat/roadmap-implementation` 一致，工作区干净。通过只读 SSH 确认容器 `3654a3fd7d8a` / `pgvector` 位于既有虚拟机，而非 Windows 本机；宿主机 5433 可达。容器 running，无 Docker HEALTHCHECK；`pg_isready` 接受连接。日志显示复用旧数据目录，数据库为 PostgreSQL 16.15 / pgvector 0.8.6。

原应用库只读检查：7,198 行（eval/dubbo 240、eval/rocketmq 210、freeisle/12306 1,576、freeisle/ragent 5,172），全部存在向量，类型 vector(1024)，HNSW cosine 索引存在。不把容器启动等同于历史数据已完整恢复。

创建独立 `mc_test_pgvector_20261025`；测试连接仅存在 ignored 本地文件并通过 `MC_TEST_DATABASE_URL` 传递，不改原 `.env`。测试拒绝非 `mc_test_` 库，逐例创建和清理随机 schema。另保留独立 bootstrap 测试库，实际验证从无 vector 扩展开始连续初始化两次成功。

### 数据库修复与专项验证

提交 `06ac60a`：修复注册 vector 类型早于 CREATE EXTENSION 的新库初始化错误；upsert 批次改为事务原子提交，校验 embedding 数量，类型注册失败关闭连接，连接超时 5 秒。现有 delete_by_path 与 upsert 之间仍是两个独立事务，不扩大承诺。

`uv run pytest -q tests/test_pgvector.py tests/test_dedup_postgres.py` → **8 passed / 0 failed / 0 skipped**。真实 vector(1024) 写读/维度校验、cosine 排序、仓库与 ID 隔离、精确通道、批次失败回滚、异常后恢复、HNSW 查询计划均验证；备份/恢复测试已替换旧 double precision[]，验证完整向量行、相似记录保留、跨仓库、幂等及失败回滚。确定性向量只验证 SQL，不作为模型质量证据。

### 历史副本演练

`uv run python -m scripts.verify_legacy_copy`：只读复制全部 7,198 行到测试库随机 schema；ragent 预览 79 条别名，其余三个仓库 0。副本清理、再次执行无候选、完整行恢复、再次恢复零新增均通过；恢复后全行摘要与复制前一致，HNSW 可用，原库前后摘要一致。副本与备份保留在测试库供审查。报告：`eval/reports/pgvector-legacy-copy.json`。

原库历史索引没有执行迁移/清理，故「减少重复入库」仍为部分完成；真实语料副本演练已经解除 vector 类型兼容、备份恢复和事务回滚的验证阻塞。原库实施仍需明确授权和独立可恢复备份。

### 完整回归的环境纠正

首次 `uv run pytest -q` → **161 passed / 1 failed / 0 skipped**。失败为已有 Java MCP 进程：当前 PATH 选中 JDK 8，报 UnsupportedClassVersionError（class file 61，运行时最高 52）。仅在后续测试进程设置已有 JDK 21 的 JAVA_HOME/PATH；未修改断言、未跳过 Java 测试、未改系统默认 Java。

复现步骤和安全边界见 `docs/postgres-validation.md`。Skill 语义反馈、Langfuse 完整自托管和业务后训练状态不受本次实库验收影响。

纠正 Java 运行时后的 `uv run pytest -q` → **162 passed / 0 failed / 0 skipped**，耗时 259.86 秒；仅 1 条 Starlette/anyio 依赖弃用警告。Java 模块未改动，实际 Python→Spring Boot MCP 测试随全量通过，因此本轮不另做 Maven 构建。`python -m compileall -q src scripts eval tests` 通过。

### 固定语料与真实检索设置

构建/验证工具提交 `4946d63`。`uv run python -m scripts.prepare_retrieval_db --reuse-legacy-vectors` 成功构建固定 6,349-chunk 语料（12306 1,539 / ragent 4,611 / 本项目 199），全部 vector(1024)。与离线对照相同 snapshot SHA256：`d17e7ad54a4608e01e752b6d6cba97d1d445be6c6dc400062a6d2f7e1a65fafa`。

6,114 条复用同 repo/全文的历史向量，235 条使用当前 BGE-M3 重算（219 个唯一 repo/text）。历史中 63 个键存在不同向量，全部从复用候选排除；这 63 个键并非全部出现在本轮快照中，不把它们计作重算数量。抽查 3 个复用向量与当前模型的 cosine 均大于 0.99999999999。历史 baseline 声明 BGE-M3，但没有逐行模型版本记录；抽查不能代替全量溯源。语料报告生成时库代码为 `06ac60a`，准备脚本尚在工作区，脚本及报告随后纳入 `4946d63`。

本地模型 revision：BGE-M3 `5617a9f61b028005a4858fdac845db406aefb181`；BGE-reranker-v2-m3 `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`。HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE 开启，CPU / OMP_NUM_THREADS=4 / MKL_NUM_THREADS=4；模型使用现有默认精度、截断和打分。RRF K=60，候选池 50，最终 k=8，关闭外部 LLM 改写，保留本地 rerank。

三仓库的参数化 cosine SQL 抽查均返回请求的 50 条。实际优化器在此规模采用 Seq Scan 或 repo Bitmap Scan 后距离排序，不声称本轮所有检索请求都走 HNSW；HNSW 存在及可执行已在专项和历史副本强制索引计划中验证。计划见 `eval/reports/pgvector-plan.json`，已省略向量字面值。pytest 随机 schema 最后检查为 0 个残留；历史演练 schema 和 bootstrap 测试库主动保留。


第一次 n=24 混合检索在默认重排 batch_size=32 下出现显著本机内存压力（可用不足 1 GB），完成的前两条均 hit@8=1 / MRR=1 / Recall@8=1；主动停止该未完成试跑，日志仅保留在 ignored data，不作为完整评测。为降低峰值增加 `--rerank-batch-size` 参数，默认仍为 32，本轮改为 4；不改模型精度、截断、候选池和标签。后续必须重新完整执行全部 24 条，不能拼接试跑结果。

在评测 public.chunks 已创建后，全量回归暴露测试自身依赖：临时改表名的故障注入会沿 search_path 找到 public 同名表，因此没有触发 UndefinedTable（162 passed / 1 failed）。改为在测试 schema 的表内临时改 embedding 列名，严格断言 UndefinedColumn，恢复列后重新验证检索；没有放宽断言或跳过错误路径。测试连接使用脱敏 repr，避免失败报告展示 DSN。此修改后 PostgreSQL 专项再跑 **8 passed / 0 failed / 0 skipped**。


批量配置提交 `baf5ead`；测试隔离/脱敏修复提交 `7d5b77d`。最终代码 `uv run pytest -q` → **163 passed / 0 failed / 0 skipped**，耗时 138.53 秒，1 条既有依赖弃用警告。8 项真实 PostgreSQL 测试和真实 Java MCP 进程测试全部包含在内。未修改 Java 模块，因此未额外执行 Maven。


### 最终检索结果与本轮状态

主评测命令 `uv run python -m eval.run_eval --suite retrieval --dataset retrieval-v2 --no-rewrite --rerank-batch-size 4`，**24/24 实际完成，无跳过/通道错误**：Hit@3=0.7917、Recall@5=0.7569、MRR@8=0.6806、Hit@8=0.8750、Recall@8=0.7917。报告 `docs/eval-report/retrieval-20261025-172045.md`。代码 SHA baf5ead，后续 7d5b77d 仅测试改动。

同库同语料增加 `--no-rerank`：**24/24 实际完成**，Hit@3=0.6667、Recall@5=0.7282、MRR@8=0.5951；报告 `docs/eval-report/retrieval-20261025-172414.md`。全量设置、依赖版本、期望/实际文档与分通道 top-8 均记录。仅该消融可用于判断本固定集 rerank 的影响，不把 n=8 历史 baseline、n=24 离线和 n=24 混合的差值当作单一优化收益。

主评测未命中 ret-004/007/014；ret-004 无 rerank 可召回第 4 位相关文档，启用后丢失；其余两者两种模式均未命中。总体 rerank 提升三项指标，但未修复全部难例。详见 `eval/reports/retrieval-pgvector-v2.md`。

最终只读审计：原库 7,198 行完整摘要未变，测试语料 6,349 行全部带向量，24 条查询标注文档全部存在，pytest 随机 schema 残留为 0。历史恢复副本另抽查 5 组别名/规范记录，通过 EXPLAIN ANALYZE 确认实际 HNSW 执行，5/5 均在 top-20 中找回两条记录；补充证据已写入 legacy-copy 报告。

本轮将召回覆盖优化的实库验收标记完成；Roadmap 当前 8 项完成、4 项仍部分完成。减少重复入库仍保留 `[ ]`：原库清理需独立授权与可恢复备份，本轮仅操作测试库。Skill 真实反馈语义收益、Langfuse 完整部署、业务后训练继续保留未完成。

功能/工具/测试阶段提交：06ac60a、4946d63、baf5ead、7d5b77d；最终报告与 Roadmap 状态纳入后续文档提交。完整 Python 回归最终为 163 passed / 0 failed / 0 skipped，Java 未修改，不重复 Maven 构建。所有原始连接、日志、模型、快照、数据库数据均不进入 Git。
