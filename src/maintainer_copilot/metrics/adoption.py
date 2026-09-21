"""HITL 决策事件采集：草稿采纳率的真实数据源。

设计:
- 只在 Executor 记录(它是图中唯一知道闸门决策结果的位置):
  人工决策 approve/edit/reject 各记一条事件
- 自审降级(degrade)不是人工决策, 不记录
- 数字只来自真实演示/生产操作, 绝不预填(简历指标红线)
- 存储: data_dir/adoption.sqlite(已 gitignore), 由 mc adoption 汇总
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS hitl_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    repo TEXT NOT NULL,
    issue_number INTEGER,
    worker TEXT NOT NULL,
    decision TEXT NOT NULL,
    note TEXT
)
"""

ADOPTED_DECISIONS = ("approved", "edited")


class DecisionStore:
    """SQLite 持久化 HITL 决策事件, 提供采纳率汇总。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(
        self,
        *,
        repo: str,
        issue_number: int | None,
        worker: str,
        decision: str,
        note: str = "",
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO hitl_decisions (ts, repo, issue_number, worker, decision, note)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (ts, repo, issue_number, worker, decision, note[:300]),
            )

    def summary(self) -> dict:
        """采纳率汇总: 采纳 = 批准 + 编辑; 驳回计入分母。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT worker, decision, COUNT(*) AS n FROM hitl_decisions"
                " GROUP BY worker, decision"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM hitl_decisions").fetchone()[0]
        by_worker: dict[str, dict[str, int]] = {}
        for row in rows:
            by_worker.setdefault(row["worker"], {})[row["decision"]] = row["n"]
        adopted = sum(
            n for counts in by_worker.values() for decision, n in counts.items()
            if decision in ADOPTED_DECISIONS
        )
        return {
            "total_decisions": total,
            "adopted": adopted,
            "rejected": total - adopted,
            "adoption_rate": round(adopted / total, 3) if total else None,
            "by_worker": by_worker,
        }


_store: DecisionStore | None = None


def get_store() -> DecisionStore:
    """模块级单例, 路径来自 settings.data_dir(测试可整体替换 settings)。"""
    global _store
    if _store is None:
        _store = DecisionStore(get_settings().data_dir / "adoption.sqlite")
    return _store


def reset_store_for_tests(store: DecisionStore | None = None) -> None:
    """测试专用: 替换单例(测试隔离, 不污染真实数据文件)。"""
    global _store
    _store = store
