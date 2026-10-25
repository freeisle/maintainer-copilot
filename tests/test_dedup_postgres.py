"""Real vector(1024) backup/restore in a random schema of a dedicated test DB."""
from types import SimpleNamespace
import psycopg
import pytest
from scripts import dedup_index
from maintainer_copilot.rag.indexer import DDL


@pytest.fixture
def database(pg_database, monkeypatch):
    monkeypatch.setattr(dedup_index, 'get_settings', lambda: SimpleNamespace(database_url=pg_database))
    with psycopg.connect(pg_database) as c:
        c.execute(DDL)
        for identity, repo, path in [('old','a/b','guide.md'), ('canonical','a/b','docs/guide.md'), ('other','c/d','guide.md')]:
            c.execute("INSERT INTO chunks VALUES (%s,%s,'doc',%s,'same','{\"heading\":\"x\",\"part_idx\":0}','hash','commit',%s::vector)", (identity,repo,path,'['+','.join(['0.1']*1024)+']'))
    return pg_database


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


def test_similar_documents_and_other_repos_survive(database):
    with psycopg.connect(database) as c:
        for identity, path, content, heading in [
            ('different-text', 'guide.md', 'same but different', 'x'),
            ('different-heading', 'guide.md', 'same', 'another'),
            ('unrelated-path', 'other.md', 'same', 'x'),
        ]:
            c.execute("INSERT INTO chunks SELECT %s,repo,source,%s,%s,jsonb_build_object('heading',%s::text,'part_idx',0),content_hash,commit_sha,embedding FROM chunks WHERE id='canonical'",
                      (identity,path,content,heading))
        before = c.execute('SELECT * FROM chunks ORDER BY id').fetchall()
    assert dedup_index.run('a/b')['aliases'] == {'old': 'canonical'}
    batch = dedup_index.run('a/b', apply=True)['backup_batch']
    assert dedup_index.run('a/b', apply=True) == {'aliases': {}, 'applied': False}
    with psycopg.connect(database) as c:
        assert c.execute('SELECT count(*) FROM chunks').fetchone()[0] == 5
        assert c.execute('SELECT count(*) FROM chunks WHERE vector_dims(embedding)=1024').fetchone()[0] == 5
        c.execute('SET LOCAL enable_seqscan=off')
        plan = c.execute('EXPLAIN SELECT id FROM chunks ORDER BY embedding <=> (SELECT embedding FROM chunks LIMIT 1) LIMIT 2').fetchall()
        assert 'idx_chunks_embedding' in str(plan)
    assert dedup_index.run('a/b', restore=batch)['restored'] == 1
    with psycopg.connect(database) as c:
        assert c.execute('SELECT * FROM chunks ORDER BY id').fetchall() == before
