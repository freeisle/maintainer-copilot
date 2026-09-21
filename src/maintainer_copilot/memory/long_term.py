"""长期记忆：repo 偏好(版本化 SQLite) + append-only 事件历史 + 冲突升级 HITL。

存储: data/memory/memory.sqlite3(已 gitignore, 测试用 tmp data_dir 隔离)
- preferences: 当前生效值, 每次写入 version+1
- preference_events: append-only 全历史(只增不删, 可追溯)
- pending_conflicts: 新偏好与存量矛盾时挂起, 人工 CLI 裁决前对外使用默认值

设计原则: 新偏好与既有偏好矛盾时绝不静默覆盖——挂起待维护者裁决,
历史全部可追溯(面试卖点: 偏好冲突的 HITL 升级路径)。
"""
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    repo TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_ts TEXT NOT NULL,
    PRIMARY KEY (repo, key)
);
CREATE TABLE IF NOT EXISTS preference_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    repo TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    action TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_conflicts (
    repo TEXT NOT NULL,
    key TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (repo, key)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(repo: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", repo)


def _db_path() -> Path:
    p = get_settings().data_dir / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p / "memory.sqlite3"


def _migrate_legacy_json(conn: sqlite3.Connection) -> None:
    """一次性迁移旧版 JSON 存储(data/memory/{slug}.json), 迁移后改名 .migrated。"""
    legacy_dir = get_settings().data_dir / "memory"
    for path in legacy_dir.glob("*.json"):
        try:
            mem = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        repo = mem.get("repo") or path.stem
        prefs: dict = mem.get("preferences", {})
        for key, value in prefs.items():
            if key.startswith("_") and key.endswith("_pending"):
                real_key = key[1:-8]  # _reply_language_pending -> reply_language
                old = prefs.get(real_key)
                if old is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO pending_conflicts"
                        " (repo, key, old_value, new_value, ts) VALUES (?, ?, ?, ?, ?)",
                        (repo, real_key, json.dumps(old, ensure_ascii=False),
                         json.dumps(value, ensure_ascii=False), _now()),
                    )
            elif not key.startswith("_"):
                conn.execute(
                    "INSERT OR IGNORE INTO preferences"
                    " (repo, key, value, version, updated_ts) VALUES (?, ?, ?, 1, ?)",
                    (repo, key, json.dumps(value, ensure_ascii=False), _now()),
                )
        for event in mem.get("history", []):
            conn.execute(
                "INSERT INTO preference_events (ts, repo, key, value, action, source)"
                " VALUES (?, ?, ?, ?, 'set', ?)",
                (event.get("ts", _now()), repo, event.get("key", ""),
                 json.dumps(event["value"], ensure_ascii=False) if "value" in event else None,
                 event.get("source", "legacy")),
            )
        path.rename(path.with_suffix(".json.migrated"))


def _db() -> sqlite3.Connection:
    # isolation_level=None: autocommit(逐条提交)。Python 3.13 默认非自动提交,
    # close() 会回滚未提交的写入, 曾导致偏好写不进去(测试抓出)。
    conn = sqlite3.connect(_db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate_legacy_json(conn)
    return conn


def get_preference(repo: str, key: str, default: Any = None) -> Any:
    """读取生效偏好; 该键存在待裁决冲突时返回 default(裁决前使用默认值)。"""
    with closing(_db()) as conn:
        pending = conn.execute(
            "SELECT 1 FROM pending_conflicts WHERE repo=? AND key=?", (repo, key)
        ).fetchone()
        if pending is not None:
            return default
        row = conn.execute(
            "SELECT value FROM preferences WHERE repo=? AND key=?", (repo, key)
        ).fetchone()
    return json.loads(row["value"]) if row is not None else default


def update_preference(repo: str, key: str, value: Any, source: str = "maintainer") -> dict:
    """写入偏好; 与既有值矛盾时挂起冲突(不覆盖), 待人工裁决。"""
    with closing(_db()) as conn:
        old_row = conn.execute(
            "SELECT value, version FROM preferences WHERE repo=? AND key=?", (repo, key)
        ).fetchone()
        old = json.loads(old_row["value"]) if old_row is not None else None
        if old_row is not None and old != value:
            conn.execute(
                "INSERT OR REPLACE INTO pending_conflicts"
                " (repo, key, old_value, new_value, ts) VALUES (?, ?, ?, ?, ?)",
                (repo, key, json.dumps(old, ensure_ascii=False),
                 json.dumps(value, ensure_ascii=False), _now()),
            )
            conn.execute(
                "INSERT INTO preference_events (ts, repo, key, value, action, source)"
                " VALUES (?, ?, ?, ?, 'conflict_pending', ?)",
                (_now(), repo, key, json.dumps(value, ensure_ascii=False), source),
            )
            return {"conflict": True, "old": old, "new": value, "applied": False}
        version = (old_row["version"] if old_row is not None else 0) + 1
        conn.execute(
            "INSERT OR REPLACE INTO preferences (repo, key, value, version, updated_ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (repo, key, json.dumps(value, ensure_ascii=False), version, _now()),
        )
        conn.execute(
            "INSERT INTO preference_events (ts, repo, key, value, action, source)"
            " VALUES (?, ?, ?, ?, 'set', ?)",
            (_now(), repo, key, json.dumps(value, ensure_ascii=False), source),
        )
        return {"conflict": False, "applied": True, "version": version}


def resolve_conflict(repo: str, key: str, keep: str) -> dict:
    """人工裁决挂起冲突: keep=old|new; 裁决后版本递增, 事件留痕。"""
    with closing(_db()) as conn:
        pending = conn.execute(
            "SELECT old_value, new_value FROM pending_conflicts WHERE repo=? AND key=?",
            (repo, key),
        ).fetchone()
        if pending is None:
            return {"conflict": False}
        conn.execute("DELETE FROM pending_conflicts WHERE repo=? AND key=?", (repo, key))
        value = pending["new_value"] if keep == "new" else pending["old_value"]
        old_row = conn.execute(
            "SELECT version FROM preferences WHERE repo=? AND key=?", (repo, key)
        ).fetchone()
        version = (old_row["version"] if old_row is not None else 0) + 1
        conn.execute(
            "INSERT OR REPLACE INTO preferences (repo, key, value, version, updated_ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (repo, key, value, version, _now()),
        )
        conn.execute(
            "INSERT INTO preference_events (ts, repo, key, value, action, source)"
            " VALUES (?, ?, ?, ?, ?, 'maintainer')",
            (_now(), repo, key, value, f"resolve_{keep}"),
        )
        return {"conflict": False, "resolved": keep, "version": version}


def pending_conflicts() -> list[dict]:
    """待裁决冲突队列(CLI 裁决面板数据源)。"""
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT repo, key, old_value, new_value, ts FROM pending_conflicts ORDER BY ts"
        ).fetchall()
    return [
        {
            "repo": row["repo"],
            "key": row["key"],
            "old": json.loads(row["old_value"]),
            "new": json.loads(row["new_value"]),
            "ts": row["ts"],
        }
        for row in rows
    ]


def preference_history(repo: str, key: str) -> list[dict]:
    """append-only 事件历史(按时间序, 永不删除)。"""
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT ts, value, action, source FROM preference_events"
            " WHERE repo=? AND key=? ORDER BY id",
            (repo, key),
        ).fetchall()
    return [dict(row) for row in rows]


def load_memory(repo: str) -> dict[str, Any]:
    """兼容视图(旧版 JSON 调用方): 返回 {preferences, history}。"""
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT key, value FROM preferences WHERE repo=?", (repo,)
        ).fetchall()
        events = conn.execute(
            "SELECT ts, key, value, action, source FROM preference_events"
            " WHERE repo=? ORDER BY id",
            (repo,),
        ).fetchall()
    return {
        "preferences": {row["key"]: json.loads(row["value"]) for row in rows},
        "history": [dict(row) for row in events],
    }
