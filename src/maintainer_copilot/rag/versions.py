"""Isolated, immutable index generations with resumable transactional batches.

No implicit migration of public.chunks. The administrative API deliberately only
accepts mc_test_ databases until production migration is separately reviewed.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import perf_counter

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pgvector.psycopg import register_vector

from .chunkers import Chunk
from .indexer import DDL, Vector, chunk_id, content_hash


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True)
class ModelSpec:
    model: str
    revision: str | None
    dimension: int = 1024
    runtime: str = ''
    normalized: bool = True

    def verified(self):
        if not self.model or not self.revision or not re.fullmatch(r'[0-9a-f]{40}', self.revision):
            raise ValueError('New vectors require an exact model commit revision')
        if self.dimension != 1024 or not self.runtime:
            raise ValueError('Expected vector(1024) and explicit runtime configuration')


def reusable(old: dict, new: ModelSpec, old_hash: str, new_hash: str) -> bool:
    """Unknown revisions and dimension-only matches are never reusable."""
    return bool(new.revision and old.get('revision') and old == asdict(new) and old_hash == new_hash)


@dataclass
class SourceDocument:
    repo: str
    path: str
    source: str
    text: str
    chunks: list[Chunk]
    commit: str | None = None

    @property
    def key(self):
        return digest(canonical([self.repo, self.source, self.path]))


class IndexVersions:
    def __init__(self, database_url: str, registry: str = 'mc_index_registry'):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,50}', registry):
            raise ValueError('Invalid registry schema')
        self.url, self.registry = database_url, registry

    def connect(self):
        conn = psycopg.connect(self.url, connect_timeout=5)
        if not conn.execute('SELECT current_database()').fetchone()[0].startswith('mc_test_'):
            conn.close()
            raise ValueError('Index administration restricted to mc_test_ database')
        conn.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(self.registry)))
        return conn

    def migrate(self):
        with self.connect() as c:
            c.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
            c.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(self.registry)))
            c.execute('''CREATE TABLE IF NOT EXISTS versions (
                version text PRIMARY KEY, schema_name text UNIQUE NOT NULL,
                status text NOT NULL CHECK(status IN ('building','failed','ready')),
                config jsonb NOT NULL, manifest_hash text NOT NULL,
                expected integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
                error jsonb, report jsonb);
                CREATE TABLE IF NOT EXISTS bindings (
                name text PRIMARY KEY, version text NOT NULL REFERENCES versions(version));
                CREATE TABLE IF NOT EXISTS switches (
                id bigserial PRIMARY KEY, name text NOT NULL, previous text,
                target text NOT NULL, switched_at timestamptz NOT NULL DEFAULT now());''')

    def get(self, version):
        with self.connect() as c:
            row = c.execute('SELECT schema_name,status,config,manifest_hash,expected,report FROM versions WHERE version=%s', (version,)).fetchone()
            if not row:
                raise KeyError(version)
            return dict(zip(('schema','status','config','manifest_hash','expected','report'),row))

    def scoped_url(self, version):
        record = self.get(version)
        if record['status'] != 'ready':
            raise ValueError('Index is not ready')
        return make_conninfo(self.url, options=f"-c search_path={record['schema']},public")

    def activate(self, version, name='evaluation', expected_current=None):
        """Compare-and-swap pointer and audit event in one transaction."""
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(self.registry+':'+name,))
            row=c.execute('SELECT status FROM versions WHERE version=%s FOR UPDATE',(version,)).fetchone()
            if not row or row[0] != 'ready':
                raise ValueError('Cannot activate an incomplete generation')
            row=c.execute('SELECT version FROM bindings WHERE name=%s FOR UPDATE',(name,)).fetchone()
            previous=row[0] if row else None
            if previous != expected_current:
                raise ValueError('Active generation changed; switch rejected')
            c.execute('INSERT INTO bindings VALUES (%s,%s) ON CONFLICT(name) DO UPDATE SET version=excluded.version',(name,version))
            c.execute('INSERT INTO switches(name,previous,target) VALUES (%s,%s,%s)',(name,previous,version))
        return previous

    def active(self, name='evaluation'):
        with self.connect() as c:
            row=c.execute('SELECT version FROM bindings WHERE name=%s',(name,)).fetchone()
            if not row:
                raise KeyError(name)
            return row[0]

    def build(self, version: str, *args, **kwargs):
        self.migrate()
        with self.connect() as lock:
            lock.execute('SELECT pg_advisory_lock(hashtext(%s))',(self.registry+':'+version,))
            lock.commit()
            try:
                return self._build(version,*args,**kwargs)
            finally:
                lock.execute('SELECT pg_advisory_unlock(hashtext(%s))',(self.registry+':'+version,))

    def _build(self, version: str, documents: list[SourceDocument], model: ModelSpec,
              chunker_version: str, embedder, batch_size=4, reuse_from=None,
              after_batch=None, before_write=None):
        model.verified()
        if not version or not chunker_version or batch_size < 1:
            raise ValueError('Version, chunker and positive batch size required')
        if getattr(embedder,'model',None) != model.model or getattr(embedder,'revision',None) != model.revision:
            raise ValueError('Embedder identity differs from declared model')
        # Prepare a deterministic complete manifest before any database mutation.
        docs=sorted(documents,key=lambda d:d.key)
        if len({d.key for d in docs}) != len(docs):
            raise ValueError('Duplicate source document')
        rows=[]
        ids=set()
        for d in docs:
            for ch in d.chunks:
                if not ch.text.strip():
                    raise ValueError('Empty chunk')
                if str(ch.meta.get('path','')) != d.path or ch.source != d.source:
                    raise ValueError('Chunk/source identity mismatch')
                cid=chunk_id(d.repo,ch)
                if cid in ids:
                    raise ValueError('Conflicting chunk identity')
                ids.add(cid)
                rows.append((cid,d,ch,digest(ch.text)))
        config=dict(model=asdict(model),chunker_version=chunker_version,
                    identity_version='logical-location-v1',batch_size=batch_size)
        manifest=digest(canonical([(d.key,digest(d.text),d.commit,[(chunk_id(d.repo,ch),digest(ch.text),ch.meta) for ch in d.chunks]) for d in docs]))
        schema='mc_idx_'+digest(self.registry+':'+version)[:24]
        self.migrate()
        with self.connect() as c:
            old=c.execute('SELECT config,manifest_hash,status FROM versions WHERE version=%s',(version,)).fetchone()
            if old and (old[0] != config or old[1] != manifest):
                raise ValueError('Generation is immutable; changed input needs a new version')
            if old and old[2]=='ready':
                return dict(self.get(version)['report'],idempotent=True)
            c.execute('INSERT INTO versions(version,schema_name,status,config,manifest_hash,expected) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(version) DO UPDATE SET status=excluded.status,error=NULL',
                      (version,schema,'building',canonical(config),manifest,len(rows)))
            c.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
            c.execute(sql.SQL('SET LOCAL search_path TO {}, public').format(sql.Identifier(schema)))
            c.execute(DDL)
            c.execute('''CREATE TABLE IF NOT EXISTS documents (
                document_key text PRIMARY KEY,repo text NOT NULL,source text NOT NULL,path text NOT NULL,
                source_content_hash text NOT NULL,commit_sha text,expected integer NOT NULL);
                ALTER TABLE chunks ADD COLUMN IF NOT EXISTS document_key text REFERENCES documents(document_key);
                ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_content_hash text;
                ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedded_at timestamptz;
                CREATE TABLE IF NOT EXISTS completed_batches (batch integer PRIMARY KEY, chunk_ids jsonb NOT NULL);''')
            for d in docs:
                c.execute('INSERT INTO documents VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(document_key) DO NOTHING',
                          (d.key,d.repo,d.source,d.path,digest(d.text),d.commit,len(d.chunks)))
        start=perf_counter()
        stats=dict(documents=len(docs),chunks=len(rows),embedded=0,reused=0,resumed=0,
                   reuse_source=reuse_from,
                   embedding_seconds=0.,write_seconds=0.,committed_batches=0,
                   empty_chunks=0,identity_duplicates=0,dimension_errors=0,failures=0)
        current=[]
        try:
            for offset in range(0,len(rows),batch_size):
                batch=offset//batch_size;current=rows[offset:offset+batch_size]
                with self.connect() as c:
                    c.execute(sql.SQL('SET LOCAL search_path TO {}, public').format(sql.Identifier(schema)))
                    done=c.execute('SELECT chunk_ids FROM completed_batches WHERE batch=%s',(batch,)).fetchone()
                    if done:
                        if done[0] != [r[0] for r in current]:
                            raise ValueError('Resume batch mismatch')
                        stats['resumed']+=len(current)
                        continue
                vectors={}
                vector_dates={}
                if reuse_from:
                    old=self.get(reuse_from)
                    if old['status']=='ready' and old['config'].get('chunker_version')==chunker_version:
                        with self.connect() as c:
                            register_vector(c)
                            found=c.execute(sql.SQL('SELECT id,chunk_content_hash,embedding,embedded_at FROM {}.chunks WHERE id=ANY(%s)').format(sql.Identifier(old['schema'])),([r[0] for r in current],)).fetchall() if old['config'].get('model',{}).get('revision') else []
                        wanted={r[0]:r[3] for r in current}
                        vectors={cid:v.to_list() if hasattr(v,'to_list') else v for cid,h,v,at in found if v is not None and at is not None and reusable(old['config']['model'],model,h,wanted[cid])}
                        vector_dates={cid:at for cid,h,v,at in found if cid in vectors}
                missing=[r for r in current if r[0] not in vectors]
                tick=perf_counter()
                encoded=embedder.embed_documents([r[2].text for r in missing]) if missing else []
                stats['embedding_seconds']+=perf_counter()-tick
                if len(encoded)!=len(missing):
                    raise ValueError('Embedding count mismatch')
                vectors.update({r[0]:v for r,v in zip(missing,encoded)})
                for v in vectors.values():
                    if len(v)!=model.dimension or not all(math.isfinite(float(x)) for x in v) or not any(v):
                        stats['dimension_errors']+=1
                        raise ValueError('Invalid vector dimension, zero or nonfinite value')
                tick=perf_counter()
                with self.connect() as c:
                    register_vector(c)
                    c.execute(sql.SQL('SET LOCAL search_path TO {}, public').format(sql.Identifier(schema)))
                    for i,(cid,d,ch,h) in enumerate(current):
                        if before_write:
                            before_write(batch,i,c)
                        c.execute('INSERT INTO chunks(id,repo,source,path,content,meta,content_hash,commit_sha,embedding,document_key,chunk_content_hash,embedded_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                  (cid,d.repo,d.source,d.path,ch.text,canonical(ch.meta),content_hash(ch.text),d.commit,Vector(vectors[cid]),d.key,h,vector_dates.get(cid,datetime.now(timezone.utc))))
                    c.execute('INSERT INTO completed_batches VALUES (%s,%s)',(batch,canonical([r[0] for r in current])))
                stats['write_seconds']+=perf_counter()-tick
                stats['embedded']+=len(missing);stats['reused']+=len(current)-len(missing)
                stats['committed_batches']+=1
                if after_batch:
                    after_batch(batch)
            with self.connect() as c:
                c.execute(sql.SQL('SET LOCAL search_path TO {}, public').format(sql.Identifier(schema)))
                actual=c.execute('SELECT id,chunk_content_hash,content,vector_dims(embedding),document_key,embedded_at FROM chunks').fetchall()
                if {r[0] for r in actual} != ids or any(digest(r[2])!=r[1] or r[3]!=1024 or not r[4] or not r[5] for r in actual):
                    raise ValueError('Index integrity validation failed')
                counts=c.execute('SELECT d.document_key,d.expected,count(c.id) FROM documents d LEFT JOIN chunks c USING(document_key) GROUP BY d.document_key,d.expected').fetchall()
                if any(expected!=n for _,expected,n in counts) or len(counts)!=len(docs):
                    raise ValueError('Incomplete source coverage')
                stats['per_repo']=[dict(repo=r,documents=n,chunks=k) for r,n,k in c.execute('SELECT repo,count(*),sum(expected) FROM documents GROUP BY repo ORDER BY repo')]
                stats['storage_bytes']=c.execute("SELECT pg_total_relation_size('chunks')").fetchone()[0]
                stats['content_duplicate_groups']=c.execute('SELECT count(*) FROM (SELECT repo,path,chunk_content_hash FROM chunks GROUP BY repo,path,chunk_content_hash HAVING count(*)>1) t').fetchone()[0]
                stats.update(elapsed_seconds=perf_counter()-start,orphan_chunks=0,unknown_revisions=0)
                c.execute(sql.SQL('UPDATE {}.versions SET status=%s,report=%s WHERE version=%s').format(sql.Identifier(self.registry)),('ready',canonical(stats),version))
            return stats
        except BaseException as exc:
            # Persist identifiers, never raw exception strings that may contain DSNs.
            with self.connect() as c:
                c.execute('UPDATE versions SET status=%s,error=%s WHERE version=%s',('failed',canonical(dict(type=type(exc).__name__,chunk_ids=[r[0] for r in current])),version))
            raise

    def preserve_legacy(self, version, source_schema='public'):
        """Copy a test baseline, preserving unknown provenance rather than guessing it."""
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,50}',source_schema):
            raise ValueError('Invalid schema')
        self.migrate()
        schema='mc_idx_'+digest(self.registry+':'+version)[:24]
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(self.registry+':'+version,))
            if c.execute('SELECT 1 FROM versions WHERE version=%s',(version,)).fetchone():
                raise ValueError('Legacy copy already exists')
            c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            c.execute(sql.SQL('CREATE TABLE {}.chunks (LIKE {}.chunks INCLUDING ALL)').format(sql.Identifier(schema),sql.Identifier(source_schema)))
            c.execute(sql.SQL('INSERT INTO {}.chunks SELECT * FROM {}.chunks').format(sql.Identifier(schema),sql.Identifier(source_schema)))
            count,fp=c.execute(sql.SQL("SELECT count(*),md5(string_agg(md5(to_jsonb(chunks)::text),'' ORDER BY id)) FROM {}.chunks").format(sql.Identifier(schema))).fetchone()
            config=dict(model=dict(model=None,revision=None,dimension=1024),chunker_version=None,provenance='legacy-unknown-unverified')
            c.execute('INSERT INTO versions(version,schema_name,status,config,manifest_hash,expected,report) VALUES (%s,%s,%s,%s,%s,%s,%s)',(version,schema,'ready',canonical(config),fp,count,canonical(dict(chunks=count,unknown_revisions=count))))
        return dict(chunks=count,fingerprint=fp)


class VersionedRetriever:
    """Resolve one generation per query; keep lexical caches generation-local."""
    def __init__(self, versions, embedder, reranker, binding='evaluation', allow_unknown=False, **strategies):
        self.versions,self.embedder,self.reranker=versions,embedder,reranker
        self.binding,self.allow_unknown,self.strategies=binding,allow_unknown,strategies
        self.cache={}

    async def retrieve(self, query, repo, **kwargs):
        from .retriever import HybridRetriever
        v=self.versions.active(self.binding)
        record=self.versions.get(v)
        model=record['config'].get('model',{})
        if not model.get('revision') and not self.allow_unknown:
            raise ValueError('Historical query compatibility unverified; explicit opt-in required')
        if model.get('revision') and (model['revision']!=self.embedder.revision or model['model']!=self.embedder.model):
            raise ValueError('Query embedding model incompatible with index')
        if v not in self.cache:
            self.cache[v]=HybridRetriever(self.versions.scoped_url(v),self.embedder,self.reranker,**self.strategies)
        result=await self.cache[v].retrieve(query,repo,**kwargs)
        for d in result:
            d['index_version']=v
        return result
