# PostgreSQL / pgvector retrieval-v2 实测（2026-10-25）

## 结论与口径

实际 PostgreSQL 16.15 / pgvector 0.8.6，独立测试数据库，6,349 条固定语料、3 仓库、24 条查询。使用真实 BGE-M3 / pgvector cosine + BM25 / 路径信号 + 精确匹配 + RRF + BGE-reranker-v2-m3；不是离线词法或模型替身。

| 结果 | n | Hit@3 | Recall@5 | MRR@8 |
|---|---:|---:|---:|---:|
| 历史混合 baseline（含 LLM 改写） | 8 | 0.7500 | 0.5208 | 0.5417 |
| v2 离线词法 legacy | 24 | 0.5000 | 0.5407 | 0.4379 |
| v2 离线词法 enhanced | 24 | 0.6250 | 0.5903 | 0.5486 |
| 本轮真实 PostgreSQL 混合 + rerank | 24 | 0.7917 | 0.7569 | 0.6806 |
| 同库同语料，仅关闭 rerank | 24 | 0.6667 | 0.7282 | 0.5951 |

本轮 Hit@8=0.8750、Recall@8=0.7917；3 条完全未命中，8 条 Recall@5 未达到 1。保留所有样本，不将部分命中统计为完整召回。

历史 n=8 使用不同语料/索引状态且启用 LLM 改写，不能直接宣称同口径提升。两个 n=24 离线结果与本轮使用相同数据集、固定快照和指标截断，可以比较整套后端表现；通道与模型不同，差值不能归因于单个优化。未修改任何评测标签或历史报告。

## 可复现设置

- 实际命令：`uv run python -m eval.run_eval --suite retrieval --dataset retrieval-v2 --no-rewrite --rerank-batch-size 4`。
- `MC_DATABASE_URL` 仅在评测进程中指向专用测试库；原 `.env` 不变。HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE 开启；CPU，OMP/MKL 线程数各 4。
- 代码 SHA：`baf5ead66a5121a3744c3e10c9e3fe7a32d4b7d2`。后续 `7d5b77d` 仅修改测试隔离与脱敏，不改变检索实现。
- 运行时间：2026-10-25 16:46:14 至 17:20:45（UTC+8）；单次完整运行，不声称统计显著性。
- k=8 / rerank_pool=50 / RRF K=60；不启用 LLM 改写。重排批量 4，默认仍为 32，未更改模型精度、截断长度或候选池。此前 batch=32 的两条试跑被停止，未混入本报告。
- BGE-M3 revision `5617a9f61b028005a4858fdac845db406aefb181`；BGE-reranker-v2-m3 revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`。
- pgvector 镜像：`pgvector/pgvector@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`。
- 数据集 SHA256：`db8404eafabb33495cc27a36f1531eba0a9037823b5e98ca4c65a90f9a33acea`。
- 固定快照 SHA256：`d17e7ad54a4608e01e752b6d6cba97d1d445be6c6dc400062a6d2f7e1a65fafa`。复评应保留快照；直接从当前源码 `--build` 会改变语料，不能声称同口径。
- 6,114 条复用同 repo/全文历史向量；235 条重算（219 个唯一 repo/text）。历史冲突键排除；3 个复用向量兼容性抽查通过。历史向量缺逐行模型版本记录，抽查不代替全量溯源。

[完整逐查询报告](../../docs/eval-report/retrieval-20261025-172045.md)、[语料/向量摘要](pgvector-corpus.json)、[查询计划](pgvector-plan.json)、[数据库安全审计](pgvector-final-audit.json)、[历史副本恢复证据](pgvector-legacy-copy.json)。三仓库抽查均返回 50 个向量候选；此规模优化器采用 repo 过滤后距离排序，HNSW 可执行性及恢复后查询另行验证，不冒称每次检索必定走 HNSW。

## 完全未命中的查询

通道诊断记录的是各通道 top-8，而非完整 50 候选池。单通道 top-8 未命中不能证明标注文档完全不在候选池。

### ret-004：文档入库 Pipeline 有哪些节点？

期望文档：

- `rag/src/main/java/com/nageoffer/ai/ragent/ingestion/engine/IngestionEngine.java`
- `rag/src/main/java/com/nageoffer/ai/ragent/ingestion/node/ChunkerNode.java`
- `rag/src/main/java/com/nageoffer/ai/ragent/ingestion/node/EnhancerNode.java`
- `rag/src/main/java/com/nageoffer/ai/ragent/ingestion/node/EnricherNode.java`
- `rag/src/main/java/com/nageoffer/ai/ragent/ingestion/node/FetcherNode.java`
- `rag/src/main/java/com/nageoffer/ai/ragent/ingestion/node/IndexerNode.java`
- `rag/src/main/java/com/nageoffer/ai/ragent/ingestion/node/ParserNode.java`

实际 top-8（按顺序）：

1. `issue#95`
2. `rag/src/test/java/com/nageoffer/ai/ragent/ingestion/service/impl/IngestionTaskServiceImplTest.java`
3. `README.md`
4. `docs/examples/pdf-ingestion-example.md`
5. `issue#45`
6. `issue#85`
7. `frontend/src/pages/admin/ingestion/IngestionPage.tsx`
8. `rag/src/main/java/com/nageoffer/ai/ragent/ingestion/service/impl/IngestionPipelineServiceImpl.java`

原查询通道 top-8 命中：exact=False, dense=True, sparse=False。

### ret-007：知识库命中率统计是怎么实现的？

期望文档：

- `frontend/src/services/dashboardService.ts`
- `rag/src/main/java/com/nageoffer/ai/ragent/admin/service/impl/DashboardServiceImpl.java`

实际 top-8（按顺序）：

1. `rag/src/test/java/com/nageoffer/ai/ragent/admin/service/impl/DashboardServiceImplTest.java`
2. `rag/src/main/java/com/nageoffer/ai/ragent/rag/core/retrieval/channel/KeywordSearchChannel.java`
3. `rag/src/main/java/com/nageoffer/ai/ragent/rag/service/stat/KbRetrievalStatRecorder.java`
4. `rag/src/main/java/com/nageoffer/ai/ragent/rag/core/retrieval/channel/GraphSearchChannel.java`
5. `rag/src/main/java/com/nageoffer/ai/ragent/knowledge/service/impl/KnowledgeBaseServiceImpl.java`
6. `rag/src/test/java/com/nageoffer/ai/ragent/rag/rewrite/QueryRewriteTests.java`
7. `rag/src/test/java/com/nageoffer/ai/ragent/rag/core/retrieval/channel/GraphSearchChannelTest.java`
8. `rag/src/main/java/com/nageoffer/ai/ragent/rag/core/retrieval/channel/RetrievalScopeResolver.java`

原查询通道 top-8 命中：exact=False, dense=False, sparse=False。

### ret-014：chooseSeats 传空字符串为什么抛 StringIndexOutOfBoundsException？

期望文档：

- `ai-agent-py/app/tools/order_tools.py`

实际 top-8（按顺序）：

1. `services/ticket-service/src/main/java/org/opengoofy/index12306/biz/ticketservice/service/handler/ticket/select/TrainSeatTypeSelector.java`
2. `services/ticket-service/src/main/java/org/opengoofy/index12306/biz/ticketservice/service/handler/ticket/TrainFirstSleeperPurchaseTicketHandler.java`
3. `services/ticket-service/src/main/java/org/opengoofy/index12306/biz/ticketservice/service/handler/ticket/TrainSecondSleeperPurchaseTicketHandler.java`
4. `services/ticket-service/src/main/java/org/opengoofy/index12306/biz/ticketservice/service/handler/ticket/select/SeatSelection.java`
5. `services/ticket-service/src/main/java/org/opengoofy/index12306/biz/ticketservice/service/handler/ticket/TrainBusinessClassPurchaseTicketHandler.java`
6. `issue#18`
7. `services/ticket-service/src/main/java/org/opengoofy/index12306/biz/ticketservice/service/handler/ticket/base/TrainBusinessCheckSeat.java`
8. `services/ticket-service/src/main/java/org/opengoofy/index12306/biz/ticketservice/service/handler/ticket/TrainSecondClassPurchaseTicketHandler.java`

原查询通道 top-8 命中：exact=False, dense=False, sparse=False。

## 部分召回与排序损失

| 查询 | Hit@3 | Recall@5 | MRR@8 |
|---|---:|---:|---:|
| ret-003 | 0 | 0.3333 | 0.2500 |
| ret-005 | 1 | 0.3333 | 0.3333 |
| ret-006 | 1 | 0.5000 | 0.5000 |
| ret-013 | 1 | 0.5000 | 0.5000 |
| ret-015 | 0 | 1.0000 | 0.2500 |
| ret-021 | 1 | 0.5000 | 1.0000 |

这些误差保留为后续回归点；本轮未更改 k、标注或按样本硬编码排序。

## 同口径 rerank 消融与瓶颈

实际命令：`uv run python -m eval.run_eval --suite retrieval --dataset retrieval-v2 --no-rewrite --no-rerank --rerank-batch-size 4`，运行于 2026-10-25 17:22:28–17:24:14（UTC+8）。[完整消融报告](../../docs/eval-report/retrieval-20261025-172414.md)。除了关闭 rerank，数据库、语料、查询、embedding、RRF、候选池和截断不变；代码 SHA 的差异仅为测试修复。

本轮同口径观察：启用 rerank 后 Hit@3 增加 0.1250，Recall@5 增加 0.0287，MRR@8 增加 0.0855；Hit@8 均为 0.8750。是本固定集的单次对照，不是普遍收益或统计显著性声明。

- **ret-004，重排损失有直接证据**：无 rerank 时 IngestionEngine.java 排第 4（Recall@5=1/7），有 rerank 时相关文档全部掉出 top-8。模型将 issue、测试和示例排在真实节点实现之前。应围绕多节点问题、短实现片段与测试文件偏好建立更广的回归，而非只增加 top_k；本轮保留原模型与策略。
- **ret-007，召回/融合前段不足**：两种模式均未命中，原查询 dense/sparse top-8 均不含 Dashboard 标注文档。中文“命中率统计”更偏向测试和统计记录器。当前诊断不能证明标注文档不在全部 50 候选中，因此不将责任完全归于 embedding；后续可做完整候选池及查询改写对照。
- **ret-014，跨语言意图歧义**：两种模式均未命中。实际结果集中在 Java 选座处理器，期望为 Python order_tools.py 中的参数防御。错误串通道未命中；保留该困难样本，后续扩大调用端参数校验场景覆盖，不修改标签来迎合检索。
- **排序与多文档覆盖**：ret-003、ret-015 首个相关文档位于第 4，ret-005/006/013/021 仍有部分 Recall@5 缺失；完整期望/实际文档均在机器报告。

## 本轮验收边界

真实 PostgreSQL 混合链路与同库消融均实际执行，外部数据库连接阻塞已解除。原库 7,198 行摘要未变；历史副本的 79 条别名已验证清理/完整恢复，5 组恢复记录的实际 HNSW 查询通过。原库历史清理未执行，也未执行原库备份恢复操作；不能把这些证据写成原库迁移完成。Skill 语义反馈、Langfuse 自托管和业务后训练不在本轮完成范围。
