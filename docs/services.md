# 可选知识服务与联邦检索

默认仍为单进程 pgvector 混合检索。只在配置中列出的关联仓库参与联邦查询，例如：

```bash
MC_RETRIEVAL_BACKEND=local
MC_LOCAL_INDEX_PATH=data/retrieval-snapshot.jsonl
MC_FEDERATION_REPOS={"freeisle/ragent":["freeisle/12306"]}
```

使用 `mc ask --repo freeisle/ragent "问题"` 即进入同一个 Solver。默认不跨库；关联范围不接受 LLM 动态扩大。最多 8 仓库、4 并发，每源 15 秒超时；按各源名次融合避免直接比较不同索引的分数。
每条引用携带 repo/path/source，跨库内容仅作为证据，不改变任务的写入目标。部分来源失败在文档元数据和日志中标识；全部失败抛错，不返回伪造的空成功。

## A2A 独立知识服务

服务进程设置 `MC_A2A_ALLOWED_REPOS=["freeisle/ragent"]`，启动：

```bash
python -m uvicorn maintainer_copilot.services.a2a:create_app --factory --host 127.0.0.1 --port 8010
```

调用进程设置 `MC_A2A_URL=http://127.0.0.1:8010` 后，原 `mc ask` 的 Solver 检索通过服务发现进入远程知识进程。两端可设置同一 `MC_A2A_TOKEN` 启用 Bearer 认证；不要把 token 写入仓库。默认仅本机开发，外部部署需 TLS 与网关访问控制。
服务发现 `/.well-known/agent-card.json`，JSON-RPC endpoint `/a2a`；实现 `message/send`、`tasks/get`、`tasks/cancel`，固定协议 [A2A 0.3 specification](https://a2a-protocol.org/v0.3.0/specification/)。DataPart 使用 `repo/query/top_k`，结果为 completed Task 的 evidence artifact。
只读同步短任务，最多保留 128 个任务供查询，重启后不承诺任务历史；完成任务取消返回规范错误，未知任务、无效参数、未知方法、检索超时均有明确错误。未宣称流式、推送或完整协议一致性认证。
客户端不自动重试，校验发现 endpoint 与配置同源后才发送凭据；服务限定仓库、4 并发和总等待超时。真实独立进程互操作由 `tests/test_a2a.py` 验证。


## Java MCP 工具服务

场景：PR 审阅时检查 Maven 声明，不启动 Maven、不下载依赖、不读取服务端文件。传入 POM 文本，返回直接坐标、java.version 和依赖声明（不解析 parent/BOM/property，因此 resolved=false）。

```powershell
mvn -B -f services/java-tools/pom.xml test package
java -jar services/java-tools/target/java-tools-0.1.0.jar
uv run python scripts/inspect_pom.py services/java-tools/pom.xml
uv run pytest tests/test_java_mcp.py -q
```

服务固定只监听 127.0.0.1:8020，Streamable HTTP `/mcp`；Python 支持 `--url`，总超时 10 秒，不重试。使用现有 FastMCP 客户端，显式兼容 initialize 握手，关闭环境代理避免本机调用被代理截获。返回兼容结构化结果与 TextContent JSON。XXE/DOCTYPE 禁用、文本长度限制 200000；不适合作为无认证公网服务。

Java 构建产物不入 Git。CI 先构建 Java 再跑 Python 测试，缺 jar 的独立 Python 环境会明确 skip 跨语言项。本轮实测没有跳过。
