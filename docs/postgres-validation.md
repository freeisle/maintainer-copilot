# PostgreSQL / pgvector 实库验收

仅在独立数据库执行本节写入。测试连接变量为 `MC_TEST_DATABASE_URL`，应用连接为 `MC_DATABASE_URL`（由 Settings 加载 `.env`）。数据库名称必须以 `mc_test_` 开头；pytest 为每项创建随机 schema，只删除自己创建的 schema。未提供测试连接时明确 skip，不代表通过。

```powershell
# 从本机受保护、被 Git 忽略的文件读取连接，不能把连接串写入报告或命令历史
$env:MC_TEST_DATABASE_URL=Get-Content data/pgvector-test-connection.txt -Raw
uv run pytest -q tests/test_pgvector.py tests/test_dedup_postgres.py
uv run pytest -q
```

专项使用真实 PostgreSQL、`vector(1024)` 和 HNSW。确定性向量只用于验证 SQL 排序、隔离和异常路径，不能作为模型质量证据。dedup 测试覆盖预览、保留相似文档/其他仓库、完整向量行备份恢复、恢复不覆盖更新、删除失败事务回滚、重复清理幂等和 HNSW 可用性。

入库批次在事务内原子提交；连接/类型注册失败及时关闭连接。新数据库先启用扩展再建表，不提前注册尚不存在的向量类型。增量 `delete_by_path` 与后续 upsert 仍是独立操作，不承诺两者跨调用原子性。

## 固定语料混合检索

需要已有 `data/retrieval-snapshot.jsonl` 及 BGE-M3 / BGE-reranker-v2-m3 模型缓存。语料来源及 SHA256 见离线报告。准备脚本仅接受没有 public.chunks 的专用测试库，拒绝覆盖；中断后不自动删除或重建数据库。

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
uv run python -m scripts.prepare_retrieval_db --reuse-legacy-vectors
# 下面只在当前进程切换应用检索连接，不改 .env
$env:MC_DATABASE_URL=$env:MC_TEST_DATABASE_URL
$env:OMP_NUM_THREADS='4'
$env:MKL_NUM_THREADS='4'
uv run python -m eval.run_eval --suite retrieval --dataset retrieval-v2 --no-rewrite
```

可省略 `--reuse-legacy-vectors` 全量重算。启用时原应用库以只读事务读取，按 repo + 全文匹配复用历史向量；相同键存在冲突则重新计算。抽查三行与当前模型 cosine≥0.999；此抽查并非全量模型溯源证明。报告保存复用/重算数量、向量摘要和数据集 hash，不保存向量或凭据。原库不执行清理。

评测固定 k=8，额外记录 Recall@5；记录通道文档、失败样本、模型名、代码 SHA、数据库/扩展版本。关闭外部 LLM 改写使本轮无付费 API 依赖。与历史 n=8（含改写）不能直接比较；与 n=24 词法对照使用同一语料，但算法通道不同，只作后端对照，不能归因于单一优化。

## 历史数据副本演练

保持 MC_DATABASE_URL 为原应用库、MC_TEST_DATABASE_URL 为专用测试库，执行：

```powershell
uv run python -m scripts.verify_legacy_copy
```

脚本只读复制原库到测试库随机 `legacy_copy_*` schema，逐仓库预览/清理/恢复，对完整行摘要做一致性校验，并检查 HNSW 查询计划和原库未变。副本保留供审查；不执行 DROP DATABASE，不清理原库。报告 `eval/reports/pgvector-legacy-copy.json` 只含数量、随机 schema 名和查询计划，不含原始业务行。

2026-10-25 实测：7,198 行、4 仓库；ragent 79 条别名，其他仓库 0。所有恢复后摘要一致，原库摘要未变。异常回滚由前述真实 pgvector pytest 的故障注入验证。

本机 PATH 当前默认 JDK 8，既有 Spring Boot 服务需 Java 17+。本轮完整回归仅在测试进程中使用已有 JDK 21：

```powershell
$env:JAVA_HOME='E:\develop\jdk-21.0.12.1'
$env:PATH="$env:JAVA_HOME\bin;$env:PATH"
uv run pytest -q
```
