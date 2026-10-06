# Retrieval 基线评测报告（retrieval-baseline）

> 本报告只建立基线：**未修改 retrieval 逻辑、未调参数、未做任何优化**。
> 原始机器报告：`docs/eval-report/retrieval-20261005-230848.md`（打分轮）、`docs/eval-report/retrieval-check-20261005-171944.md`（标注核验轮，不计分）。

## 运行元信息

| 项目 | 值 |
|---|---|
| 测试时间 | 2026-10-05 22:09–23:08（打分轮）；16:48–17:19（标注核验轮，不计分） |
| 是否单次运行 | 是（n=8 各检索 1 次；无多次运行、无方差估计） |
| 随机因素 | 查询改写为外部 API 生成（`deepseek-chat`, temperature=0.0, max_tokens=200）。temperature=0 但外部服务跨运行不保证逐字一致；**打分轮未记录改写文本**，失败分析中的改写为同参数复现重跑（代表性强但不保证与打分轮逐字相同）。embedding / BM25 / RRF(K=60) / rerank 均为本地确定性计算 |
| 环境 | PostgreSQL 16 + pgvector @ VM(192.168.101.68:5433)；模型缓存 F 盘（bge-m3 + bge-reranker-v2-m3） |

### 模型配置

| 环节 | 配置 |
|---|---|
| 查询改写 LLM | `deepseek-chat`（primary），`temperature=0.0`；兜底 `qwen2.5:7b`（本轮未触发） |
| Embedding | `BAAI/bge-m3`（本地，pgvector HNSW cosine） |
| 稀疏召回 | 进程内 `rank_bm25`（BM25Okapi，按 repo 惰性构建） |
| 重排 | `BAAI/bge-reranker-v2-m3`（本地 CrossEncoder） |

### 评测口径（与线上 SolverWorker 一致）

- 原问题永远保留 + LLM 改写最多追加 2 条；多查询检索后 `merge_results` 按 rerank 分去重合并，取 top-8；
- 三通道（报错串精确 LIKE / pgvector 稠密 / BM25 稀疏）RRF 融合（K=60）→ rerank；
- 文档键口径：issue chunk 为 `issue#编号`，代码/文档 chunk 为仓库相对路径。

## Dataset

- **规模：n=8**
- **字段**：`id / repo / question / relevant / note`。标注字段为 **`relevant`**（与 `eval/metrics.py:doc_key` 口径一致）；任务模板中「expected_sources」为占位描述，实际实现与数据集均用 `relevant`，**未改动**。
- **来源**：两个真实开源 Java 仓库的代码/文档/issue 问答场景，标注经源码人工核对（每条的标注依据在数据集 `note` 字段）。
- **仓库分布**：`freeisle/ragent` 6 条（ret-003~008），`freeisle/12306` 2 条（ret-001/002）。
- **标注形态**：单/多标注混合，1~7 条/问（ret-004「Pipeline 有哪些节点」标注 7 个文件，为 recall 视角的多标注条目）。
- **知识库规模**：chunks 共 7198 —— ragent：code 4309 / doc 753 / issue 110；12306：code 1471 / doc 82 / issue 23。
- **标注核验结论**：8 条标注均在库（无「标注不存在」；ret-008 的 `issue#58` 已确认收录）。

## Metrics

| 指标 | 值 |
|---|---|
| Hit@1 | **0.375**（3/8：ret-002 / ret-006 / ret-008 首条即命中） |
| Hit@3 | **0.750**（6/8） |
| Hit@5 | **0.750**（与 Hit@3 持平） |
| Hit@8 | **0.750**（6/8） |
| Recall@1 | 0.250 |
| Recall@3 | **0.5208** |
| Recall@5 | **0.5208**（与 Recall@3 持平） |
| Recall@8 | **0.5208** |
| MRR | **0.5417**（MRR@3 = MRR@5 = MRR@8，全部命中均发生在 rank≤3） |

逐条明细：

| ID | 问题 | 标注数 | hit@8 | MRR | Recall@8 | 分通道命中(原问) |
|---|---|---|---|---|---|---|
| ret-001 | 候补购票怎么实现 | 3 | 1.0 | 0.50 | 0.67 | dense✓ sparse✓ |
| ret-002 | 卧铺铺位偏好分配 | 2 | 1.0 | 1.00 | 1.00 | dense✓ sparse✓ |
| ret-003 | 向量检索怎么实现 | 3 | 1.0 | 0.33 | 0.33 | 全✗（融合后命中） |
| ret-004 | 文档入库 Pipeline 节点 | 7 | 0.0 | 0.00 | 0.00 | dense✓ sparse✓ |
| ret-005 | 意图识别怎么做 | 3 | 1.0 | 0.50 | 0.67 | dense✓ |
| ret-006 | Milvus 数据源配置 | 2 | 1.0 | 1.00 | 0.50 | dense✓ |
| ret-007 | 知识库命中率统计 | 2 | 0.0 | 0.00 | 0.00 | 全✗ |
| ret-008 | QueryEmbedding 重复计算 | 1 | 1.0 | 1.00 | 1.00 | dense✓ sparse✓ |

**两个结构性观察（如实）**：

1. **Recall@3 = Recall@5 = Recall@8**：丢失主要发生在**召回层**（通道根本没召回），而非截断层。提高 k 对当前基线无收益。
2. **改写贡献可量化**：标注核验轮（无改写）top-8 标注命中 7/23 ≈ 0.30，打分轮（含改写）Recall@8 = 0.52，改写净贡献 ≈ +0.21。ret-005 从核验轮 1/3 → 0.67、ret-008 不变、ret-004 两轮均为 0（改写未救回）。

## Failure Analysis

共 6 个失败样本（Recall<1.0），按任务要求分类。证据来源：打分轮 top-8 排名（机器报告）+ 复现诊断（改写输出、分通道 top-10 排名、缺失 chunk 内容形态）。

### 1. ret-004「文档入库 Pipeline 有哪些节点」→ rerank问题（Recall=0.00，7/7 全丢）

- **现象**：通道层已召回（dense 原问：`IndexerNode.java`@4；dense 英文改写：`IngestionEngine.java`@6；sparse 原问：`IngestionEngine.java`@7），但最终 top-8 一个相关文档都没有；top-8 被 `frontend/.../ingestionService.ts`、`IngestionPage.tsx`、`IngestionPipelineServiceImpl.java` 等「ingestion pipeline」表面词文档占据。
- **归因**：① RRF 融合稀释——每个相关文档只在「单一查询 × 单一通道」组合中排名 4~7，9 条排名列表（3 查询 × 3 通道）等权融合后，单列表弱命中（1/(60+rank)）不敌多列表平庸重复命中的 surface 文档；② 重排加剧——reranker 青睐「pipeline」整体性文档，节点类文件被挤出；③ 结构上限——7 标注 × k=8，即便全部召回也需要 7/8 席位，且每个节点文件被切成 4~5 个 chunk（按 path 计为一键，召回粒度与标注粒度错位）。
- **类别**：rerank问题（主因）；多标注截断为数据侧结构性因素。

### 2. ret-007「知识库命中率统计怎么实现」→ query rewrite问题（Recall=0.00，2/2 全丢）

- **现象**：中文原问与中文改写对 dense/sparse 通道**零命中**；唯一能召回的是英文改写 `knowledge base hit rate statistics implementation`（dense：`DashboardServiceImpl.java`@3、`dashboardService.ts`@6；sparse：ts@6）。打分轮最终 top-8 两个标注都未进入——`DashboardServiceImplTest.java` 反而排第 1（测试类包含「命中率」测试场景，文本相似度高）。
- **归因**：代码面标识符是英文领域词（Dashboard / hitRate / performance），中文「知识库命中率统计」与其无语义重叠，中文 embedding 检索存在**中英语义沟**；改写虽补出英文查询但只有 1/2 条补对了方向，且改写的英文查询与实现文件的相关性仍弱于测试文件（测试文件密集出现「命中率」字样）。
- **类别**：query rewrite问题（改写不足以稳定弥合中英语义沟）+ embedding召回问题（次因）。

### 3. ret-003「向量检索怎么实现」→ embedding召回问题（Recall=0.33，丢 2/3）

- **现象**：`VectorSearchChannel.java` 对英文改写 dense 排名 1~3（强命中），但 `VectorRetrieverService.java` 与 `docs/releases/v1.1.0.md` 在**任何通道任何查询**下都没进 top-10。最终 top-8 被 README、issue#58/#38 占位。
- **归因**：① `VectorRetrieverService.java` 的 chunk 内容密度极低（5 个 chunk 长度 239/40/61/85/71 字符，多为接口 default 方法骨架），embedding 语义信号弱、与「实现细节」类问题不匹配；② `v1.1.0.md` 为发布说明，对「实现」类问题本身相关性弱——**属标注口径问题**（弱标注），该文件被切为 5 个碎 chunk（157/78/82/203/7 字符）进一步稀释。
- **类别**：embedding召回问题（主因）+ 标注口径（release 文档弱标注）。

### 4. ret-006「Milvus 数据源怎么配置」→ chunk切分问题（Recall=0.50，丢 1/2）

- **现象**：`resources/docker/lightweight/README.md` dense 排名 1~2 强命中（最终 top-8 第 1），但 `MilvusVectorRetrieverService.java` 仅英文改写 dense 命中 @10（超出 top-8 截断），所有中文查询 0 命中。
- **归因**：① 该文件被切成 5 个细碎 chunk（608/356/134/83/288 字符），单 chunk 语义密度不足，「Milvus 数据源/配置」主题信号被切散，向量区分度低于同主题的 `MilvusConfig.java`（最终第 2）、`MilvusVectorStoreAdmin/Service.java`（第 5/6）；② 标注口径上，「数据源怎么配置」的最优答案本就更接近 README + MilvusConfig，接口实现文件相关性次之——该标注偏弱。
- **类别**：chunk切分问题（粒度过细稀释语义）+ embedding召回问题（次因）+ 标注口径。

### 5. ret-005「意图识别怎么做」→ embedding召回问题（Recall=0.67，丢 1/3）

- **现象**：`DefaultIntentClassifier.java`（最终第 3）与 `IntentResolver.java`（最终第 2）命中，`IntentGuidanceService.java` 全通道 0 召回。最终 top-8 第 1 被测试类 `SimpleIntentClassifierTests.java` 占据。
- **归因**：`IntentGuidanceService.java` 的 chunk 开头是 `fetchNode / nodeCache` 等 guidance 内部词汇（307/1131/391/232/1712 字符 × 5 chunk），与「意图识别」表面词无重叠，dense 语义相似度低；测试类文本含密集的 intent 关键词反超实现类。
- **类别**：embedding召回问题。

### 6. ret-001「候补购票怎么实现」→ BM25问题（Recall=0.67，丢 1/3）

- **现象**：`README.md`（最终第 2）与 `waitlist_scorer.py`（最终第 3）命中；`waitlist_tools.py` 全通道仅 dense 原问 @8 边缘命中一次，最终被挤出 top-8——而其**测试文件** `test_waitlist_tool.py`（第 4）、`test_waitlist_scorer.py`（第 5）双双进榜。
- **归因**：① sparse（BM25）对 `waitlist_tools.py` 实现 0 召回，而对测试文件命中——BM25 词频对「waitlist」在测试类名/用例名中的密集出现更敏感，实现文件词频稀疏；② 该实现文件仅 2 个 chunk（330/1500 字符），首个 chunk 从函数体中间开始（切分切断了函数定义与语义单元）。
- **类别**：BM25问题（主因）+ chunk切分问题（次因）。

### 全局发现：文档 chunk 双路径重复（切分/入库侧产物）

ragent 的 docs 被以两个前缀各入库一份（最终 top-8 中同时出现 `docs/releases/v1.1.0.md` 与 `releases/v1.1.0.md`、`docs/releases/v1.0.0.md` 与 `releases/v1.0.0.md`）。近似重复内容占据多个 rank 席位，加剧融合/重排竞争（ret-005 的第 6/7、ret-006 的第 3/4 席位）。**这是基线观察，未做任何改动**。

### 分类汇总

| 类别 | 样本 | 一句话机制 |
|---|---|---|
| query rewrite问题 | ret-007 | 中英语义沟，改写仅 1/2 条补对方向且弱于测试类文本 |
| chunk切分问题 | ret-006（主）、ret-001（次） | 接口/实现文件切分过细或切断语义单元，主题信号稀释 |
| embedding召回问题 | ret-003、ret-005、ret-007（次因） | 低内容密度 chunk / 表面词不重叠，向量相似度不足 |
| BM25问题 | ret-001（主） | 实现文件词频稀疏，测试文件因密集关键词反超 |
| rerank问题 | ret-004（主） | 通道弱命中被 RRF 稀释 + rerank 青睐 surface 文档挤出 |

## 复现命令

```bash
uv run python -m eval.run_eval --suite retrieval                  # 打分轮（本报告数字）
uv run python -m eval.run_eval --suite retrieval --check --no-rewrite   # 标注核验（不计分）
```
