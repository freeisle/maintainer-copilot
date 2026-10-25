"""Real pgvector IO. Deterministic vectors test SQL, not model quality."""
import psycopg
import pytest
from pgvector.psycopg import register_vector
from maintainer_copilot.rag.chunkers import Chunk
from maintainer_copilot.rag.indexer import Indexer, chunk_id
from maintainer_copilot.rag.retriever import HybridRetriever


class Vectors:
    def embed_documents(self, texts):
        return [[1.0, 0.0] + [0.0] * 1022 if t == 'needle' else
                [0.0, 1.0] + [0.0] * 1022 for t in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


@pytest.mark.asyncio
async def test_vector_schema_ranking_filters_and_idempotence(pg_database):
    index = Indexer(pg_database, Vectors())
    await index.init_schema()
    await index.init_schema()
    chunks = [Chunk('needle', 'doc', {'path': 'docs/a.md'}),
              Chunk('different', 'code', {'path': 'src/a.py'})]
    assert (await index.upsert_chunks('a/b', chunks))['inserted'] == 2
    assert (await index.upsert_chunks('a/b', chunks))['unchanged'] == 2
    await index.upsert_chunks('other/repo', chunks)
    search = HybridRetriever(pg_database, Vectors())
    search.reranker = None
    assert await search._dense('a/b', 'needle', 2) == [chunk_id('a/b', c) for c in chunks]
    results = await search.retrieve('needle', 'a/b')
    assert results[0]['path'] == 'docs/a.md'
    assert all(r['repo'] == 'a/b' for r in results)
    assert await search._fetch('other/repo', [r['id'] for r in results]) == []
    with psycopg.connect(pg_database) as conn:
        register_vector(conn)
        assert conn.execute('SELECT vector_dims(embedding) FROM chunks LIMIT 1').fetchone()[0] == 1024
        assert conn.execute("SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid='chunks'::regclass AND attname='embedding'").fetchone()[0] == 'vector(1024)'
        conn.execute('SET LOCAL enable_seqscan=off')
        plan = conn.execute("EXPLAIN SELECT id FROM chunks ORDER BY embedding <=> (SELECT embedding FROM chunks LIMIT 1) LIMIT 2").fetchall()
        assert 'idx_chunks_embedding' in str(plan)
    assert await index.delete_by_path('a/b', 'docs/a.md') == 1
    assert await index.count('other/repo') == 2
    assert await index.delete_by_path('a/b', 'docs/a.md') == 0


@pytest.mark.asyncio
async def test_failed_batch_is_atomic_and_connections_recover(pg_database):
    index = Indexer(pg_database, Vectors())
    await index.init_schema()
    chunks = [Chunk('needle', 'doc', {'path': 'a.md'}), Chunk('different', 'doc', {'path': 'b.md'})]
    with psycopg.connect(pg_database) as c:
        c.execute("ALTER TABLE chunks ADD CONSTRAINT reject_b CHECK (path <> 'b.md')")
    with pytest.raises(psycopg.errors.CheckViolation):
        await index.upsert_chunks('a/b', chunks)
    assert await index.count() == 0
    assert (await index.upsert_chunks('a/b', chunks[:1]))['inserted'] == 1
    with psycopg.connect(pg_database) as c:
        c.execute("SELECT 1")


@pytest.mark.asyncio
async def test_dimension_and_missing_embedding_rejected(pg_database):
    index = Indexer(pg_database, Vectors())
    await index.init_schema()
    chunks = [Chunk('needle', 'doc', {'path': 'a.md'})]
    index.embedder.embed_documents = lambda texts: [[1.0, 2.0]]
    with pytest.raises(psycopg.DataError):
        await index.upsert_chunks('a/b', chunks)
    assert await index.count() == 0
    index.embedder.embed_documents = lambda texts: []
    with pytest.raises(ValueError, match='count'):
        await index.upsert_chunks('a/b', chunks)
    assert await index.count() == 0


@pytest.mark.asyncio
async def test_exact_channel_and_database_error_propagation(pg_database):
    index = Indexer(pg_database, Vectors())
    await index.init_schema()
    chunk = Chunk('java.lang.NullPointerException', 'doc', {'path': 'errors.md'})
    await index.upsert_chunks('a/b', [chunk])
    await index.upsert_chunks('other/repo', [chunk])
    search = HybridRetriever(pg_database, Vectors())
    assert await search._exact('a/b', chunk.text, 5) == [chunk_id('a/b', chunk)]
    with psycopg.connect(pg_database) as c:
        # Keep the scoped table in place: a missing table could resolve to
        # public.chunks after the evaluation corpus has been prepared.
        c.execute('ALTER TABLE chunks RENAME COLUMN embedding TO embedding_unavailable')
    with pytest.raises(psycopg.errors.UndefinedColumn):
        await search._dense('a/b', 'needle', 5)
    with psycopg.connect(pg_database) as c:
        c.execute('ALTER TABLE chunks RENAME COLUMN embedding_unavailable TO embedding')
    assert await search._dense('a/b', 'needle', 5) == [chunk_id('a/b', chunk)]


@pytest.mark.asyncio
async def test_strategy_trace_real_database_isolation(pg_database):
    index = Indexer(pg_database, Vectors())
    await index.init_schema()
    chunks = [Chunk("loadValue", "code", {"path": "impl.py", "part_idx": 0}),
              Chunk("loadValue second", "code", {"path": "impl.py", "part_idx": 1}),
              Chunk("loadValue", "code", {"path": "call.java"})]
    await index.upsert_chunks("allowed/repo", chunks)
    await index.upsert_chunks("private/repo", chunks)
    r = HybridRetriever(pg_database, Vectors(), symbol_recall=True, coverage_pool=True)
    r.reranker = None
    trace = {}
    result = await r.retrieve("loadValue", "allowed/repo", rerank_pool=2, trace=trace)
    assert {x["path"] for x in result} == {"impl.py", "call.java"}
    assert all(x["repo"] == "allowed/repo" for x in trace["candidates"])
    assert len(trace["before_rerank"]) == 2
    assert trace["symbol_matches"]
    assert all(-1 <= v <= 1 for v in trace["dense_scores"].values())
    assert trace["bm25_scores"]
    assert await r._fetch("private/repo", [x["id"] for x in result]) == []
