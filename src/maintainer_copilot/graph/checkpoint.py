"""Durable single-host saver, usable from CLI and both server event loops.

Use the upstream SQLite schema/serializer. Async calls run the synchronous,
lock-protected saver on a worker thread, avoiding loop-bound connection sharing.
For multiple server hosts use PostgreSQL instead; SQLite is not a distributed DB.
"""
import asyncio
import atexit
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


class PersistentSaver(SqliteSaver):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        super().__init__(conn)
        self._closed = False
        self.setup()  # Fail at startup, never silently discard pending approvals.
        atexit.register(self.close)

    def close(self):
        with self.lock:
            if not self._closed:
                self.conn.close()
                self._closed = True
        atexit.unregister(self.close)

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for row in rows:
            yield row

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        return await asyncio.to_thread(self.delete_thread, thread_id)

    async def aget_delta_channel_history(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_delta_channel_history, *args, **kwargs)
