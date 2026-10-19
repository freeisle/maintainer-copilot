# 本地观测与 Langfuse

默认关闭；不采集问题、回答、工具参数、原始异常文本或密钥。Solver 根 span 与 LLM generation 通过 ContextVar 关联；记录模型、耗时、成功/失败和 fallback。评测 CLI 将汇总数字作为同一 evaluation trace 的 scores 上报。并发上下文隔离，失败不影响业务，不自动重试。

```powershell
$env:MC_OBSERVABILITY_ENABLED='true'
# 只配置此开关即可写 data/traces.jsonl，无 Docker/账号依赖。
uv run python -m eval.run_eval --suite retrieval --dataset retrieval-v2 --no-rewrite
```

完整 Langfuse 本地部署（需要 Docker Compose；本轮主机没有 Docker，尚未验证面板）：

```powershell
uv run python scripts/prepare_langfuse.py
docker compose -p mc-langfuse --project-directory data/langfuse -f data/langfuse/compose.yml config --quiet
docker compose -p mc-langfuse --project-directory data/langfuse -f data/langfuse/compose.yml up -d
```

脚本校验官方 v4.63.0 compose 的 SHA256，固定应用镜像 4.63.0，为独立存储生成随机密码到被 Git 忽略的 `data/langfuse/.env`，拒绝覆盖已有配置。只绑定本机 3000/9090，不暴露或复用 RAG PostgreSQL。基础设施镜像沿用上游版本标签；首次拉取后用 `docker compose ... config --resolve-image-digests > data/langfuse/compose.lock.yml` 固定全部 digest，后续部署使用 lock 文件。尚未拉取镜像，不能声称完成环境复现。备份时包含 volumes 和本地 .env，停止用 `down`（不要加 `-v`）。

打开 http://localhost:3000 注册本地用户、创建项目及 API keys，再设置：

```powershell
$env:MC_LANGFUSE_URL='http://127.0.0.1:3000'
$env:MC_LANGFUSE_PUBLIC_KEY='<项目 public key>'
$env:MC_LANGFUSE_SECRET_KEY='<项目 secret key>'
```

实现使用 [官方 OTLP/HTTP JSON 接口](https://langfuse.com/integrations/native/opentelemetry)，发送 `x-langfuse-ingestion-version: 4`，数字评分走 `/api/public/scores`。本地 JSONL 保留相同 payload 供排障，不包含 HTTP 认证头。上报超时 3 秒/请求；这是小规模同步 best-effort exporter，没有持久队列/自动重发，文件应由部署方轮转。响应失败或 partial rejection 有脱敏日志；本地成功不等于 Langfuse 接受。

本轮 `tests/test_observability.py` 使用真实本机 HTTP 接收器验证父子 trace、scores 关联和故障隔离，未把接收器冒充 Langfuse。待 Docker 可用后须验证 UI 中 trace 与 score 可见，才可勾选 roadmap。
