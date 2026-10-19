# 可选知识服务与联邦检索

默认仍为单进程 pgvector 混合检索。只在配置中列出的关联仓库参与联邦查询，例如：

```bash
MC_RETRIEVAL_BACKEND=local
MC_LOCAL_INDEX_PATH=data/retrieval-snapshot.jsonl
MC_FEDERATION_REPOS={"freeisle/ragent":["freeisle/12306"]}
```

使用 `mc ask --repo freeisle/ragent "问题"` 即进入同一个 Solver。默认不跨库；关联范围不接受 LLM 动态扩大。最多 8 仓库、4 并发，每源 15 秒超时；按各源名次融合避免直接比较不同索引的分数。
每条引用携带 repo/path/source，跨库内容仅作为证据，不改变任务的写入目标。部分来源失败在文档元数据和日志中标识；全部失败抛错，不返回伪造的空成功。
