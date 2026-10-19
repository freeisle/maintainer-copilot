"""Opt-in real PostgreSQL transaction tests; each run uses an isolated schema.

Set MC_TEST_DATABASE_URL to an expendable local test DB. Tests do not need
pgvector: a numeric array exercises complete-row backup/restore independently;
production pgvector type compatibility remains a separate deployment check.
"""
import os
from types import SimpleNamespace
import uuid
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
from scripts import dedup_index


@pytest.fixture
def database(monkeypatch):
    url=os.environ.get('MC_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set MC_TEST_DATABASE_URL for real PostgreSQL transaction tests')
    schema='roadmap_test_'+uuid.uuid4().hex
    with psycopg.connect(url) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    scoped=make_conninfo(url,options=f'-c search_path={schema}')
    monkeypatch.setattr(dedup_index,'get_settings',lambda:SimpleNamespace(database_url=scoped))
    try:
        with psycopg.connect(scoped) as c:
            c.execute('''CREATE TABLE chunks(id TEXT PRIMARY KEY,repo TEXT NOT NULL,source TEXT NOT NULL,
                         path TEXT NOT NULL,content TEXT NOT NULL,meta JSONB NOT NULL,content_hash TEXT NOT NULL,
                         commit_sha TEXT,embedding double precision[])''')
            for identity,repo,path in [('old','a/b','guide.md'),('canonical','a/b','docs/guide.md'),('other','c/d','guide.md')]:
                c.execute("INSERT INTO chunks VALUES (%s,%s,'doc',%s,'same','{\"heading\":\"x\",\"part_idx\":0}','hash','commit',ARRAY[0.1,0.2])",(identity,repo,path))
        yield scoped
    finally:
        with psycopg.connect(url) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_preview_apply_and_complete_row_restore(database):
    with psycopg.connect(database) as c:
        original=c.execute('SELECT * FROM chunks ORDER BY id').fetchall()
    assert dedup_index.run('a/b')=={'aliases':{'old':'canonical'},'applied':False}
    batch=dedup_index.run('a/b',apply=True)['backup_batch']
    with psycopg.connect(database) as c:
        assert c.execute('SELECT id FROM chunks ORDER BY id').fetchall()==[('canonical',),('other',)]
    assert dedup_index.run('a/b',restore=batch)['restored']==1
    with psycopg.connect(database) as c:
        assert c.execute('SELECT * FROM chunks ORDER BY id').fetchall()==original
    assert dedup_index.run('a/b',restore=batch)['restored']==0


def test_restore_never_overwrites_newer_row(database):
    batch=dedup_index.run('a/b',apply=True)['backup_batch']
    with psycopg.connect(database) as c:
        c.execute("INSERT INTO chunks SELECT 'old',repo,source,'guide.md','newer',meta,'newhash',commit_sha,embedding FROM chunks WHERE id='canonical'")
    assert dedup_index.run('a/b',restore=batch)['restored']==0
    with psycopg.connect(database) as c:
        assert c.execute("SELECT content FROM chunks WHERE id='old'").fetchone()[0]=='newer'


def test_delete_failure_rolls_back_backup_and_rows(database):
    with psycopg.connect(database) as c:
        c.execute("CREATE FUNCTION prevent_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected failure'; END $$")
        c.execute('CREATE TRIGGER reject_delete BEFORE DELETE ON chunks FOR EACH ROW EXECUTE FUNCTION prevent_delete()')
    with pytest.raises(psycopg.Error,match='injected failure'):
        dedup_index.run('a/b',apply=True)
    with psycopg.connect(database) as c:
        assert c.execute('SELECT count(*) FROM chunks').fetchone()[0]==3
        assert c.execute("SELECT to_regclass('chunk_alias_backups')").fetchone()[0] is None
