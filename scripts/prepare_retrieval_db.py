"""Prepare an empty dedicated test DB from the fixed offline corpus.

MC_TEST_DATABASE_URL must name mc_test_*. MC_DATABASE_URL is read-only here.
--reuse-legacy-vectors reuses exact (repo, content) vectors with explicit provenance;
missing texts use the cached BGE-M3 model. Never modifies the source database.
"""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from maintainer_copilot.config import get_settings
from maintainer_copilot.rag.embedder import Embedder
from maintainer_copilot.rag.indexer import Indexer, Vector, content_hash


def prepare(snapshot: Path, report: Path, reuse: bool):
    import torch
    torch.set_num_threads(4)
    target = os.environ['MC_TEST_DATABASE_URL']
    with psycopg.connect(target, connect_timeout=5) as c:
        db = c.execute('SELECT current_database()').fetchone()[0]
        if not db.startswith('mc_test_'):
            raise ValueError('Target database must start with mc_test_')
        if c.execute("SELECT to_regclass('public.chunks')").fetchone()[0] is not None:
            raise ValueError('Target chunks table already exists; refusing overwrite')
    rows = [json.loads(x) for x in snapshot.read_text(encoding='utf-8').splitlines()]
    vectors = {}
    source_counts = []
    conflicting = set()
    if reuse:
        with psycopg.connect(get_settings().database_url, connect_timeout=5,
                             options='-c default_transaction_read_only=on') as source:
            if source.execute('SELECT current_database()').fetchone()[0] == db:
                raise ValueError('Source and target databases must differ')
            source_counts = source.execute('SELECT repo,count(*) FROM chunks GROUP BY repo ORDER BY repo').fetchall()
            for repo, text, vector in source.execute('SELECT repo,content,embedding::text FROM chunks WHERE embedding IS NOT NULL ORDER BY id'):
                key = (repo, text)
                value = json.loads(vector)
                if len(value) != 1024:
                    raise ValueError('Legacy vector dimension mismatch')
                if key in vectors and vectors[key] != value:
                    conflicting.add(key)
                vectors[key] = value
        for key in conflicting:
            del vectors[key]
    reused = sum((r['repo'], r['text']) in vectors for r in rows)
    embedder = Embedder()
    missing = list(dict.fromkeys((r['repo'], r['text']) for r in rows if (r['repo'], r['text']) not in vectors))
    for start in range(0, len(missing), 8):
        batch = missing[start:start+8]
        vectors.update(zip(batch, embedder.embed_documents([text for _, text in batch])))
        print(f'Embedded {min(start+8,len(missing))}/{len(missing)} missing texts', flush=True)
    # Compatibility check is evidence on sampled rows, not proof of all legacy provenance.
    checks = []
    if reuse:
        import numpy as np
        for row in rows:
            key = (row['repo'], row['text'])
            if key in missing or len(row['text']) > 1000:
                continue
            old = np.array(vectors[key]); fresh = np.array(embedder.embed_query(row['text']))
            cosine = float(old @ fresh / (np.linalg.norm(old) * np.linalg.norm(fresh)))
            checks.append({'id': row['id'], 'cosine': cosine})
            if cosine < .999:
                raise ValueError('Legacy embedding sample differs from current BGE-M3')
            if len(checks) == 3:
                break
    asyncio.run(Indexer(target).init_schema())
    digest = hashlib.sha256()
    with psycopg.connect(target, connect_timeout=5) as c:
        register_vector(c)
        with c.cursor() as cur:
            for row in rows:
                vec = vectors[(row['repo'], row['text'])]
                digest.update(json.dumps([row['id'], vec], separators=(',', ':')).encode())
                cur.execute('INSERT INTO chunks VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (row['id'], row['repo'], row['source'], row['path'], row['text'],
                             json.dumps(row['meta'], ensure_ascii=False), content_hash(row['text']),
                             None, Vector(vec)))
        c.execute('ANALYZE chunks')
        counts = c.execute('SELECT repo,count(*),count(embedding) FROM chunks GROUP BY repo ORDER BY repo').fetchall()
        version = c.execute("SELECT current_setting('server_version'),(SELECT extversion FROM pg_extension WHERE extname='vector')").fetchone()
    result = dict(code_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                  database=db, postgres=version[0], pgvector=version[1], dimension=1024,
                  snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                  vector_digest=digest.hexdigest(), counts=counts, source_counts=source_counts,
                  reused_exact_repo_text_vectors=reused, newly_embedded_chunks=len(rows)-reused,
                  conflicting_legacy_keys_excluded=len(conflicting), newly_embedded_unique_texts=len(missing),
                  embedding_model='BAAI/bge-m3', compatibility_samples=checks,
                  legacy_provenance='historical baseline declares BGE-M3; only three rows recomputed' if reuse else None)
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,default=Path('data/retrieval-snapshot.jsonl'))
    p.add_argument('--report',type=Path,default=Path('eval/reports/pgvector-corpus.json'))
    p.add_argument('--reuse-legacy-vectors',action='store_true')
    a=p.parse_args()
    prepare(a.snapshot,a.report,a.reuse_legacy_vectors)
