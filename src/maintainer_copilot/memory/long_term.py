"""长期记忆：repo 语义地图 + 维护者偏好(版本化, 冲突走 HITL)。

存储: data/memory/{repo-slug}.json
冲突处理原则: 新偏好与既有偏好矛盾时, 标记 conflict 交人工确认,
绝不静默覆盖(全部历史可追溯)。
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings

_SCHEMA_VERSION = 1


def _slug(repo: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", repo)


def _path(repo: str) -> Path:
    p = get_settings().data_dir / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{_slug(repo)}.json"


def load_memory(repo: str) -> dict[str, Any]:
    path = _path(repo)
    if not path.exists():
        return {
            "schema_version": _SCHEMA_VERSION,
            "repo": repo,
            "semantic_map": {},
            "preferences": {},
            "history": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _save(repo: str, mem: dict) -> None:
    _path(repo).write_text(
        json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update_preference(repo: str, key: str, value: Any, source: str = "maintainer") -> dict:
    """写入偏好; 与既有值矛盾时返回 conflict 标记(不覆盖, 待人工裁决)。"""
    mem = load_memory(repo)
    old = mem["preferences"].get(key)
    mem["history"].append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "key": key,
            "value": value,
            "source": source,
        }
    )
    if old is not None and old != value:
        mem["preferences"][f"_{key}_pending"] = value  # 待人工确认
        result: dict = {"conflict": True, "old": old, "new": value, "applied": False}
    else:
        mem["preferences"][key] = value
        result = {"conflict": False, "applied": True}
    _save(repo, mem)
    return result


def resolve_conflict(repo: str, key: str, keep: str) -> dict:
    """人工裁决冲突: keep=old|new。"""
    mem = load_memory(repo)
    pending_key = f"_{key}_pending"
    if pending_key not in mem["preferences"]:
        return {"conflict": False}
    new_value = mem["preferences"].pop(pending_key)
    if keep == "new":
        mem["preferences"][key] = new_value
    mem["history"].append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "key": key,
            "resolved": keep,
            "source": "maintainer",
        }
    )
    _save(repo, mem)
    return {"conflict": False, "resolved": keep}
