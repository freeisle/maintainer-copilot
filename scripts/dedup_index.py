"""Preview exact legacy aliases; --apply backs up rows transactionally.

No schema rewrite or index rebuild. --restore BATCH restores without overwriting
newer records. Requires an explicit repo and uses the configured database.
"""
import argparse
import json
import uuid

import psycopg
from psycopg.rows import dict_row

from maintainer_copilot.config import get_settings
from maintainer_copilot.rag.dedup import duplicate_aliases


def run(repo: str, apply: bool = False, restore: str | None = None) -> dict:
    with psycopg.connect(get_settings().database_url, connect_timeout=5) as conn:
        if restore:
            cur = conn.execute(
                "INSERT INTO chunks SELECT (jsonb_populate_record(NULL::chunks, row_data)).* "
                "FROM chunk_alias_backups WHERE batch = %s AND repo = %s "
                "ON CONFLICT (id) DO NOTHING", (restore, repo))
            return {"restored": cur.rowcount}
        # Lock during apply so a changed row cannot be deleted using stale evidence.
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, repo, source, path, content AS text, meta FROM chunks "
                        "WHERE repo = %s AND source = 'doc'" + (" FOR UPDATE" if apply else ""),
                        (repo,))
            aliases = duplicate_aliases(cur.fetchall())
        result = {"aliases": aliases, "applied": False}
        if apply and aliases:
            batch = uuid.uuid4().hex
            conn.execute("CREATE TABLE IF NOT EXISTS chunk_alias_backups "
                         "(batch TEXT, repo TEXT, row_data JSONB NOT NULL)")
            conn.execute("INSERT INTO chunk_alias_backups SELECT %s, repo, to_jsonb(chunks) "
                         "FROM chunks WHERE repo = %s AND id = ANY(%s)",
                         (batch, repo, list(aliases)))
            conn.execute("DELETE FROM chunks WHERE repo = %s AND id = ANY(%s)",
                         (repo, list(aliases)))
            result.update(applied=True, backup_batch=batch)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--restore")
    args = parser.parse_args()
    print(json.dumps(run(args.repo, args.apply, args.restore), ensure_ascii=False, indent=2))
