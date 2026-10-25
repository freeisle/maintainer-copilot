"""Read-only clone of the legacy index, then dedup/restore inside a test schema.

The copied schema is retained for inspection; no database or source data is deleted.
"""
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from maintainer_copilot.config import get_settings
from maintainer_copilot.rag.indexer import DDL
from scripts import dedup_index


def fingerprint(conn):
    return conn.execute("SELECT count(*),md5(string_agg(md5(to_jsonb(chunks)::text),'' ORDER BY id)) FROM chunks").fetchone()


def verify():
    source_url = get_settings().database_url
    target_url = os.environ['MC_TEST_DATABASE_URL']
    schema = 'legacy_copy_' + uuid.uuid4().hex
    with psycopg.connect(target_url, connect_timeout=5) as target:
        name = target.execute('SELECT current_database()').fetchone()[0]
        if not name.startswith('mc_test_'):
            raise ValueError('Target must be a dedicated mc_test_ database')
        target.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    scoped = make_conninfo(target_url,options=f'-c search_path={schema},public')
    with psycopg.connect(source_url, options='-c default_transaction_read_only=on',connect_timeout=5) as source:
        before_source = fingerprint(source)
        with psycopg.connect(scoped) as target:
            target.execute(DDL)
            with source.cursor(name='legacy_readonly') as read:
                read.execute('SELECT id,repo,source,path,content,meta::text,content_hash,commit_sha,embedding::text FROM chunks ORDER BY id')
                with target.cursor().copy('COPY chunks FROM STDIN') as copy:
                    for row in read:
                        copy.write_row(row)
            target.execute('ANALYZE chunks')
    original_settings = dedup_index.get_settings
    dedup_index.get_settings = lambda: SimpleNamespace(database_url=scoped)
    report = {'test_database': name, 'retained_schema': schema, 'source_rows':before_source[0], 'repositories':[]}
    try:
        with psycopg.connect(scoped) as c:
            before = fingerprint(c)
            assert before == before_source
            repos = [r[0] for r in c.execute('SELECT DISTINCT repo FROM chunks ORDER BY repo')]
        for repo in repos:
            preview = dedup_index.run(repo)
            applied = dedup_index.run(repo,apply=True)
            assert applied['aliases'] == preview['aliases']
            assert dedup_index.run(repo,apply=True)['aliases'] == {}
            if applied['applied']:
                count = dedup_index.run(repo,restore=applied['backup_batch'])['restored']
                assert count == len(preview['aliases'])
                assert dedup_index.run(repo,restore=applied['backup_batch'])['restored'] == 0
            with psycopg.connect(scoped) as c:
                assert fingerprint(c) == before
            report['repositories'].append({'repo':repo, 'aliases':len(preview['aliases']), 'restore_exact':True})
        with psycopg.connect(scoped) as c:
            c.execute('SET LOCAL enable_seqscan=off')
            plan=c.execute('EXPLAIN SELECT id FROM chunks ORDER BY embedding <=> (SELECT embedding FROM chunks LIMIT 1) LIMIT 5').fetchall()
            assert 'idx_chunks_embedding' in str(plan)
            report['hnsw_plan']=[r[0] for r in plan]
        with psycopg.connect(source_url,options='-c default_transaction_read_only=on') as source:
            assert fingerprint(source) == before_source
        report['source_unchanged'] = True
    finally:
        dedup_index.get_settings = original_settings
    Path('eval/reports/pgvector-legacy-copy.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    verify()
