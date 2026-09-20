"""PendingStore 单测：SQLite 持久化与 webhook 幂等去重。"""
from pathlib import Path

from maintainer_copilot.server import PendingStore


def _make_store(tmp_path: Path) -> PendingStore:
    return PendingStore(tmp_path / "pending.sqlite")


def test_add_and_list_roundtrip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.add(
        {
            "id": "a1",
            "thread_id": "triage-a1",
            "repo": "freeisle/ragent",
            "task_type": "triage",
            "draft": "草稿",
            "citations": [{"source": "issue", "path": "#1", "snippet": "s"}],
            "reflection": {"passed": True},
        }
    )
    items = store.list()
    assert len(items) == 1
    assert items[0]["draft"] == "草稿"
    assert items[0]["citations"][0]["path"] == "#1"
    assert items[0]["status"] == "pending"


def test_update_status_and_persistence(tmp_path: Path) -> None:
    path = tmp_path / "pending.sqlite"
    store = PendingStore(path)
    store.add({"id": "a1", "thread_id": "t1", "draft": "x"})
    store.update_status("a1", "approved", {"executed": True, "dry_run": True})
    # 重新打开(模拟服务重启)验证持久化
    store2 = PendingStore(path)
    item = store2.get("a1")
    assert item["status"] == "approved"
    assert item["final_action"]["dry_run"] is True


def test_mark_delivery_idempotent(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.mark_delivery("d1", "issues", "freeisle/ragent") is True
    assert store.mark_delivery("d1", "issues", "freeisle/ragent") is False  # 重复
    assert store.mark_delivery("d2", "issues", "freeisle/ragent") is True
