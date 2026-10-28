# 索引生命周期与向量溯源

本轮管理接口只允许 `mc_test_` 数据库，在独立 registry 与 `mc_idx_*` schema 中工作。旧 `public.chunks`、业务库和原 `scripts/ingest.py` / `update_kb.py` 不自动迁移。后两者仍是未治理的旧入口；需要新版本保证时使用下述重建路径。生产接入需要独立迁移审核。

## 元数据与兼容性

- registry.versions：不可变代际名、schema、building/failed/ready、模型配置、切分器版本、完整输入清单SHA256、预期分段数、创建时间、错误标识及完成报告。
- 每代 documents：仓库/来源/路径身份、规范化源文本完整SHA256、源commit、预期分段数。Issue以独立编号为文档，完整导出对象规范化为JSON。
- 每代 chunks：保留原读取列和1024维HNSW；增加document_key外键、完整chunk_content_hash、embedded_at。旧content_hash保留以兼容旧工具。复用保留原embedded_at，完成报告记录reuse_source。
- bindings/switches：当前代际指针及切换审计。compare-and-swap检查旧指针；状态检查、更新和审计同一事务。每次查询固定一个代际，BM25缓存按代际隔离。

源文本统一UTF-8解码、CRLF转LF；源哈希针对该规范化文本，不声称是原始字节哈希。chunk ID沿用repo/source/逻辑位置生成规则，保持现有引用兼容；方法移动或行号变化可能改变ID，因此版本间完整清单替代原地残留清理。相同内容的不同位置可以合法保留，重复内容组与身份碰撞分别统计；身份碰撞直接拒绝，不能以最后一条覆盖掩盖。

模型配置包含仓库标识、精确40位commit、1024维、归一化、CPU/float32/max_seq_length、依赖版本与lock哈希。切分器版本包含实现和tree-sitter依赖。新构建必须声明精确revision，实际加载使用该revision；CLI检查CPU/精度/最大长度。API调用方须保证传入embedder遵守其声明，测试用确定性向量不是BGE质量证据。

只在来源版本ready、模型配置完全相同、切分器版本一致、chunk身份和完整内容SHA256一致、已有非空向量及生成时间时复用。源文档变化仍生成新清单；其内容不变的分段允许在上述条件下复用。模型或切分器变化则重算。未知历史revision保持null，不能由当前默认配置补齐，也不能只凭维度复用。

## 构建、恢复与切换

源输入由固定Git blob读取，不读取可变工作树；Issue导出分段与旧固定快照核对。完整清单先于写入生成。每个不可变版本对应独立schema，修改、删除、切分器或模型变更均使用新版本；旧版本保留。重复运行同一已完成输入不重新嵌入，输入漂移拒绝复用版本名。

构建按批生成向量并检查数量、维度、有限值和非零；向量、元数据和completed_batches同事务提交。失败记录当前分段ID与异常类型，保留已提交批次。进程硬退出会保留building状态，重启核对完整清单后跳过已提交批次。最终校验完整ID集合、内容SHA256、文档外键、逐文档分段数和向量维度后才能ready。未完成版本不能经管理接口查询或激活。人工破坏已提交行会导致最终校验拒绝，不自动伪造修补。

```powershell
$env:MC_TEST_DATABASE_URL = Get-Content data/pgvector-test-connection.txt -Raw
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:OMP_NUM_THREADS='4'
$env:MKL_NUM_THREADS='4'
# 先确认约4GiB可用内存；本轮停在17批/68条，下面命令保留版本名恢复。
uv run python -u -m scripts.rebuild_index --version rebuild-v2 --batch-size 4 --report data/rebuild-v2-result.json
# 输出报告必须是尚不存在的新路径，完成前不运行质量对照。
uv run python -m eval.rebuild_regression --output data/rebuild-regression-new --cache data/retrieval-real-scores.json
uv run python -m eval.audit_index_rebuild --output data/rebuild-source-audit-new.json
```

恢复必须使用原源清单、模型配置、切分器与batch_size；不要改成8或32直接接着同一版本运行。固定原输入的Git对象、导出、连接文件、模型缓存和未完成schema都是恢复所需的本地资产，不进入Git。已完成报告拒绝覆盖。

`IndexVersions.activate('v2', expected_current='v1')` 原子切换；回滚用 `activate('v1', expected_current='v2')`，旧版本不删除。生产默认Retriever没有自动接管；Solver可显式注入 `VersionedRetriever`。历史未知模型默认拒绝查询兼容性，只有在明确接受未知来源的基线实验中显式允许。全量V2尚未ready，本轮不能切换实际评测绑定；已完成的切换/回滚证据是独立实库小数据测试。

## 当前限制

主机内存不足阻塞全量6640向量与R0/R1/R2。旧Git清单也没有完整记录当时工作树：local retriever有6个内容片段差异，后续评测须披露源差异及历史向量重算两个因素。部分构建中的计时累积随进程退出丢失；最终审计仅报告数据库已提交窗口和观察到的内存峰值，不能冒充完整嵌入/写入成本。完整生产迁移条件尚未满足。
