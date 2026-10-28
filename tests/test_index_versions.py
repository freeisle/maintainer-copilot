import asyncio
from dataclasses import asdict, replace

import psycopg
import pytest
from psycopg import sql

from maintainer_copilot.rag.chunkers import Chunk
from maintainer_copilot.rag.versions import IndexVersions, ModelSpec, SourceDocument, VersionedRetriever, reusable


SPEC=ModelSpec('test-model','a'*40,runtime='deterministic-test-vectors-not-quality')


class Encoder:
    model=SPEC.model
    revision=SPEC.revision
    calls=0
    def embed_documents(self,texts):
        self.calls+=1
        return [[1.,0.]+[0.]*1022 for _ in texts]
    def embed_query(self,text):return self.embed_documents([text])[0]


def doc(text='needle',repo='a/b',path='a.py'):
    return SourceDocument(repo,path,'code',text,[Chunk(text,'code',{'path':path})])


def test_reuse_requires_verified_exact_configuration_and_content():
    old=asdict(SPEC)
    assert reusable(old,SPEC,'x','x')
    for key,value in [('revision',None),('model','another'),('runtime','changed'),('normalized',False),('dimension',768)]:
        assert not reusable(dict(old,**{key:value}),SPEC,'x','x')
    assert not reusable(old,SPEC,'x','changed')
    with pytest.raises(ValueError):replace(SPEC,revision=None).verified()


@pytest.fixture
def versions(pg_database):
    # Registry shares the already verified, temporary pytest schema.
    from psycopg.conninfo import conninfo_to_dict
    import re
    schema=re.search(r'search_path=([^,]+)',conninfo_to_dict(pg_database)['options'])[1]
    v=IndexVersions(pg_database,schema)
    yield v
    with v.connect() as c:
        if c.execute("SELECT to_regclass('versions')").fetchone()[0]:
            for (name,) in c.execute('SELECT schema_name FROM versions'):
                assert name.startswith('mc_idx_')
                c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))


def test_real_generations_update_delete_reuse_switch_and_query(versions):
    v=versions;e=Encoder();docs=[doc(),doc('other',repo='x/y')]
    result=v.build('v1',docs,SPEC,'chunker1',e,batch_size=1)
    assert result['chunks']==2 and result['embedded']==2
    calls=e.calls
    assert v.build('v1',docs,SPEC,'chunker1',e,batch_size=1)['idempotent']
    assert e.calls==calls
    v.activate('v1')
    # SQL-backed retrieval with deterministic embeddings; CE explicitly disabled.
    async def query():
        from maintainer_copilot.rag.retriever import HybridRetriever
        r=HybridRetriever(v.scoped_url('v1'),e);r.reranker=None
        hits=await r.retrieve('needle','a/b')
        assert len(hits)==1 and hits[0]['repo']=='a/b'
        assert await r._fetch('x/y',[hits[0]['id']])==[]
    asyncio.run(query())
    result=v.build('v2',[doc('modified')],SPEC,'chunker1',e,reuse_from='v1')
    assert result['chunks']==1 and result['embedded']==1  # deleted other repo absent
    assert v.active()=='v1'
    v.activate('v2',expected_current='v1')
    with pytest.raises(ValueError):v.activate('v1',expected_current='v1')
    assert v.active()=='v2'
    v.activate('v1',expected_current='v2');assert v.active()=='v1'
    class Ranker:
        def rerank(self,query,candidates,top_k=None):return [dict(d,rerank_score=1.) for d in candidates]
    gateway=VersionedRetriever(v,e,Ranker())
    async def switching_query():
        assert (await gateway.retrieve('needle','a/b'))[0]['index_version']=='v1'
        v.activate('v2',expected_current='v1')
        results=await gateway.retrieve('modified','a/b')
        assert results[0]['index_version']=='v2' and results[0]['text']=='modified'
        assert await gateway.retrieve('needle','x/y')==[]
        v.activate('v1',expected_current='v2')
        assert (await gateway.retrieve('needle','a/b'))[0]['text']=='needle'
    asyncio.run(switching_query())
    result=v.build('v3',docs,SPEC,'chunker1',e,reuse_from='v1')
    assert result['reused']==2 and result['embedded']==0
    result=v.build('v4',docs,SPEC,'chunker2',e,reuse_from='v1')
    assert result['reused']==0 and result['embedded']==2
    changed=replace(SPEC,revision='b'*40);e.revision=changed.revision
    assert v.build('v5',docs,changed,'chunker1',e,reuse_from='v1')['embedded']==2
    with pytest.raises(ValueError):v.build('v1',[doc('drift')],SPEC,'chunker1',Encoder(),batch_size=1)


def test_real_legacy_unknown_is_preserved_and_never_reused(versions):
    v=versions;e=Encoder();v.build('known',[doc()],SPEC,'c',e)
    v.preserve_legacy('unknown',v.get('known')['schema'])
    assert v.get('unknown')['config']['model']['revision'] is None
    assert v.build('new',[doc()],SPEC,'c',e,reuse_from='unknown')['embedded']==1
    v.activate('unknown')
    with pytest.raises(ValueError):asyncio.run(VersionedRetriever(v,e,object()).retrieve('needle','a/b'))


def test_real_batch_rollback_resume_and_old_index_survives(versions):
    v=versions;e=Encoder();v.build('old',[doc()],SPEC,'c',e);v.activate('old')
    docs=[doc('one',path='1.py'),doc('two',path='2.py'),doc('three',path='3.py')]
    def fail(batch,i,c):
        if batch==1:c.execute('SELECT 1/0')
    with pytest.raises(psycopg.errors.DivisionByZero):v.build('new',docs,SPEC,'c',e,batch_size=1,before_write=fail)
    assert v.active()=='old' and v.get('new')['status']=='failed'
    with pytest.raises(ValueError):v.activate('new',expected_current='old')
    with psycopg.connect(v.url) as c:
        assert c.execute(sql.SQL('SELECT count(*) FROM {}.chunks').format(sql.Identifier(v.get('new')['schema']))).fetchone()[0]==1
    report=v.build('new',docs,SPEC,'c',e,batch_size=1)
    assert report['resumed']==1 and report['embedded']==2
    def stop(batch):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):v.build('interrupted',docs,SPEC,'c',e,batch_size=1,after_batch=stop)
    assert v.build('interrupted',docs,SPEC,'c',e,batch_size=1)['resumed']==1


@pytest.mark.parametrize('mode',['dimension','count','model','nan'])
def test_real_bad_embeddings_never_ready(versions,mode):
    class Bad(Encoder):
        def embed_documents(self,texts):
            if mode=='model':raise RuntimeError('model load failed')
            if mode=='count':return []
            if mode=='nan':return [[float('nan')]+[0.]*1023]
            return [[1.]*3]
    with pytest.raises((ValueError,RuntimeError)):versions.build(mode,[doc()],SPEC,'c',Bad())
    assert versions.get(mode)['status']=='failed'
    with pytest.raises(ValueError):versions.scoped_url(mode)
