# 单机持久化与恢复

默认 `build_graph()` 使用 `MC_CHECKPOINT_DIR/checkpoints.sqlite`。安装核心依赖即包含 SQLite saver；无法初始化时启动失败，不再静默降级为内存。
使用上游 checkpoint schema 和序列化，WAL + FULL 同步 + 30 秒 busy timeout。异步接口将带锁的同步 saver 放到线程池，兼容审核台和 webhook 后台两个事件循环。
退出进程关闭连接；嵌入式调用方可显式 `graph.checkpointer.close()`。

恢复必须使用相同 `thread_id` 和相同 checkpoint 文件。审核台 pending.sqlite 保存该 ID，服务重启后从原 HumanGate 恢复；旧版纯内存挂起任务无法从磁盘重建。
备份时停服务后复制 checkpoint 及 pending 数据库，或使用 SQLite backup API；运行期间不要只复制主文件而遗漏 WAL。

运行 `pytest -q tests/test_checkpoint_persistence.py`：真实 LangGraph 使用确定性的 Worker 替代外部 LLM，第一进程挂起后退出，第二进程分别批准/编辑/驳回，并验证线程隔离和历史。

边界：该实现面向单机、单服务进程。不要把 SQLite 文件放在共享网络文件系统，不承诺跨主机高可用。GitHub 写请求与本地 checkpoint 没有分布式事务；在 GitHub 已写成功而本地尚未保存时崩溃，恢复仍可能重复发布。生产开启真实写操作前应按业务加入远端幂等校验。当前默认 dry_run 保持开启。
