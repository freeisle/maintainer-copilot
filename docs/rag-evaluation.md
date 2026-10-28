# 可复现的 RAG 诊断与消融

## 固定输入

实验只使用 `MC_TEST_DATABASE_URL` 指向已核验的 `mc_test_` 隔离库，并要求固定 6,349 行。不要修改主 `.env`，不要使用 `--build` 重建当前快照。数据库全行摘要、快照/数据集 SHA256、模型 revision、依赖 lock SHA、代码 SHA 和实现哈希进入报告；各实验之间及单次前后核验数据库摘要。原 n=8、n=24 历史报告保留。

```powershell
$env:MC_TEST_DATABASE_URL = Get-Content data/pgvector-test-connection.txt -Raw
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:OMP_NUM_THREADS='4'
$env:MKL_NUM_THREADS='4'
uv run python -m eval.retrieval_experiments --output-dir data/new-matrix --score-cache data/new-scores.json --experiments E0,E1,E2,E3,E1-symbol,E1-coverage
uv run python -m eval.retrieval_experiments --dataset retrieval-challenge --experiments E0,E3 --output-dir data/new-challenge --score-cache data/new-scores.json
uv run python -m eval.profile_retrieval --output data/new-performance.json --reference-cache data/new-scores.json
```

连接文件是本机 ignored 文件，不进入 Git；也可以由安全环境注入变量。矩阵要求起始工作区干净，源码改变即拒绝验收。输出必须为新目录/文件，`.partial.json` 不是完整实验。模型缓存按实际 revision 复用，不允许把 Mock 分数写入评测缓存；跨依赖环境应使用新的缓存文件。缓存 key 含模型/revision、torch、sentence-transformers、batch、查询和完整文本哈希；还应核验报告中的 lock SHA，不能认为这些 key 穷尽了全部环境影响。

## 三个独立开关

`HybridRetriever(symbol_recall=False, coverage_pool=False, rank_blend=False)` 的默认排序保持旧行为。`symbol_recall` 抽取显式代码符号并增加字面匹配通道；`coverage_pool` 在同样50个重排名额中优先覆盖不同文档；`rank_blend` 将文档级原融合排名与 CE 排名按 `1/(10+rank)` 等权组合。

E1 同时启用前两个，E2 只启用排名组合，E3 全开；E1-symbol/E1-coverage 分别拆开召回策略。需要业务试用时向 Solver 注入显式配置的 HybridRetriever；本轮没有修改配置工厂的默认策略。关闭对应参数即可回到原路径，不需数据库迁移。

## 诊断与指标

`retrieve(..., trace=trace)` 记录完整通道候选、dense/BM25 分、错误匹配模式、符号命中、RRF 联集、选中池、去重后、CE 后和最终覆盖结果。`eval.diagnose_retrieval` 为每个标注文档记录全部阶段位置，区分通道池缺失、融合截断、去重和排序截断。没有把 top-8 不出现等同于整个候选池不存在。结构化报告不保存 chunk 全文，但包含路径、符号、问题和分数；真实用户查询用于诊断前应注意其数据范围。

原 `eval.run_eval --suite retrieval` 也复用实际 trace 计算通道命中，不再额外调用 dense；加 `--diagnostics` 保存全池。其通道命中现在明确截取实际通道的前 k 个候选，和旧工具每个 exact 模式分别 LIMIT k 的诊断口径有细微差别，主检索结果与指标定义不变。

文件级命中不能保证片段足以回答问题。报告必须同时审查相关 symbol、错误解释、计算公式和多节点覆盖。补充挑战题的标签先核对源码证据，不能依据新排名反改；内部6题且共享部分开发集文件，不是外部独立盲测。

## 性能边界

矩阵先一次性计算查询向量并释放 Embedding 模型，再加载 Reranker；模型配置与精度不变，后续查询仍实际执行 PostgreSQL。共用候选的 CE 分按内容哈希复用，新候选真实推理。E2/E3 的回放时间不能用来宣称生产推理加速。

独立 profile 不使用 CE 分数缓存，只与旧缓存比较数值；2条查询、每配置1次，不是负载测试。P95取这2条的最大值；峰值RSS是进程生命周期累计，不能解释为每个batch独立峰值。记录进程冷加载与预热后查询；不清空操作系统文件缓存，不宣称磁盘冷启动。batch32 因已知内存风险不再试跑。模型异常、无效分数和数据库异常会显式传播，不返回伪造成功结果。

## 修复旧索引的边界

模块说明和行窗分段身份修复只影响未来入库。本轮不对原业务库或固定评测库重建/清理。未来迁移须在备份与隔离新索引中重新切分，确认文档及片段身份、向量来源和完整证据覆盖，再独立评测；不能把新语料结果混入本轮固定6,349条对照。

## 索引重建对照（2026-10-28）

新增 `eval.rebuild_regression` 以R0旧索引/E0、R1新索引/E0、R2新索引/E3比较。使用原24题和标签、相同query embedding与CE配置；每个版本报告真实快照SHA256和数据库行数/摘要，不要求新版本等于6349。原工具默认仍要求旧6349行；独立参数expected_rows/snapshot只在核验代际清单后传入。新索引未ready则拒绝对照，不能把历史指标当作本轮复现。

本轮构建因内存不足停在68/6640，R0/R1/R2尚未执行。源审计另外发现旧快照与声称Git revision有6个local retriever内容片段不一致，后续即使完成也不能把变化全部归因于切分器。未知历史向量不会复用于新构建。参见 [索引生命周期](index-lifecycle.md) 和 [阶段报告](../eval/reports/index-rebuild-v1.md)。
