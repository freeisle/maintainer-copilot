"""Rebuild a frozen source revision in a separate, resumable test generation."""
import argparse
import hashlib
import json
import os
import subprocess
from time import perf_counter
from importlib.metadata import version
from pathlib import Path

from maintainer_copilot.rag.chunkers import CodeChunker, DocChunker, IssueChunker
from maintainer_copilot.rag.embedder import Embedder
from maintainer_copilot.rag.indexer import chunk_id
from maintainer_copilot.rag.versions import IndexVersions, ModelSpec, SourceDocument, canonical, digest
from scripts.ingest import CODE_SUFFIXES, DOC_SUFFIXES

REVISION='5617a9f61b028005a4858fdac845db406aefb181'


def sources(manifest_path=Path('data/retrieval-snapshot.manifest.json')):
    """Read Git blobs, not mutable checkouts; issue exports checked against V1."""
    manifest=json.loads(manifest_path.read_text())
    old=[json.loads(line) for line in Path('data/retrieval-snapshot.jsonl').read_text(encoding='utf-8').splitlines()]
    docs=[]
    for repo,rev in sorted(manifest.items()):
        root=Path('.') if repo.startswith('local/') else Path('data/repos')/repo.split('/')[-1]
        paths=subprocess.check_output(['git','-C',str(root),'ls-tree','-r','--name-only',rev],text=True).splitlines()
        selected=[p for p in paths if Path(p).suffix in CODE_SUFFIXES|DOC_SUFFIXES and (not repo.startswith('local/') or p.startswith('src/'))]
        # cat-file batch avoids one subprocess per file and preserves exact blobs.
        proc=subprocess.run(['git','-C',str(root),'cat-file','--batch'],input=''.join(f'{rev}:{p}\n' for p in selected).encode(),capture_output=True,check=True)
        offset=0
        for name in selected:
            end=proc.stdout.index(b'\n',offset)
            header=proc.stdout[offset:end].split();size=int(header[-1])
            text=proc.stdout[end+1:end+1+size].decode('utf-8',errors='replace').replace('\r\n','\n')
            offset=end+size+2
            chunker=CodeChunker() if Path(name).suffix in CODE_SUFFIXES else DocChunker()
            chunks=chunker.split(text,name)
            docs.append(SourceDocument(repo,name,'code' if Path(name).suffix in CODE_SUFFIXES else 'doc',text,chunks,rev))
    for repo,filename in [('freeisle/ragent','nageoffer__ragent-issues.jsonl'),('freeisle/12306','nageoffer__12306-issues.jsonl')]:
        for raw in Path('data/raw',filename).read_text(encoding='utf-8').splitlines():
            issue=json.loads(raw);chunks=IssueChunker().split(issue)
            # V1 records are the authority for exported issue content.
            for ch in chunks:
                cid=chunk_id(repo,ch)
                matches=[r for r in old if r['repo']==repo and r['id']==cid]
                if len(matches)!=1 or matches[0]['text']!=ch.text:
                    raise ValueError('Issue export drift relative to fixed V1')
            path='issue#'+str(issue['number'])
            for ch in chunks: ch.meta['path']=path
            docs.append(SourceDocument(repo,path,'issue',canonical(issue),chunks,None))
    return docs,manifest


def model_spec():
    runtime=canonical(dict(normalize_embeddings=True,precision='float32',device='cpu',
                           max_seq_length=8192,torch=version('torch'),sentence_transformers=version('sentence-transformers'),
                           transformers=version('transformers'),lock_sha256=hashlib.sha256(Path('uv.lock').read_bytes()).hexdigest()))
    return ModelSpec('BAAI/bge-m3',REVISION,runtime=runtime)


def chunker_version():
    return digest(Path('src/maintainer_copilot/rag/chunkers.py').read_text(encoding='utf-8')+canonical({n:version(n) for n in ('tree-sitter','tree-sitter-java','tree-sitter-python','tree-sitter-typescript')}))


def main():
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('--version',required=True)
    p.add_argument('--registry',default='mc_rebuild_20261028')
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--reuse-from')
    p.add_argument('--plan-only',action='store_true')
    a=p.parse_args()
    if a.report.exists():raise FileExistsError(a.report)
    documents,manifest=sources()
    spec=model_spec()
    rows=[dict(id=chunk_id(d.repo,ch),repo=d.repo,path=d.path,text=ch.text,source=ch.source,meta=ch.meta) for d in documents for ch in d.chunks]
    old={r['id']:r for r in map(json.loads,Path('data/retrieval-snapshot.jsonl').read_text(encoding='utf-8').splitlines())}
    new={r['id']:r for r in rows}
    if len(new)!=len(rows):raise ValueError('Chunk identity collision')
    report=dict(code_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                source_revisions=manifest,source_documents=len(documents),chunks=len(rows),
                chunker_version=chunker_version(),model=spec.__dict__,
                source_manifest_sha256=digest(canonical([(d.key,digest(d.text)) for d in documents])),
                snapshot_sha256=digest(''.join(canonical(r)+'\n' for r in rows)),
                old_snapshot_sha256=hashlib.sha256(Path('data/retrieval-snapshot.jsonl').read_bytes()).hexdigest(),
                added=len(new.keys()-old.keys()),removed=len(old.keys()-new.keys()),
                changed=sum(new[k]['text']!=old[k]['text'] for k in new.keys()&old.keys()))
    snapshot=a.report.with_suffix('.snapshot.jsonl')
    snapshot.write_text(''.join(canonical(r)+'\n' for r in rows),encoding='utf-8')
    if not a.plan_only:
        from eval.runtime_stats import memory_bytes
        import torch
        torch.set_num_threads(4)
        embedder=Embedder(model=spec.model,revision=spec.revision)
        tick=perf_counter()
        loaded=embedder._load_local()
        report['model_load_seconds']=perf_counter()-tick
        if str(loaded.device)!='cpu' or loaded.max_seq_length!=8192 or str(next(loaded.parameters()).dtype)!='torch.float32':
            raise ValueError('Actual model runtime differs from declared provenance')
        manager=IndexVersions(os.environ['MC_TEST_DATABASE_URL'],a.registry)
        def progress(batch):
            print('committed_batch',batch,'memory',memory_bytes(),flush=True)
        report['build']=manager.build(a.version,documents,spec,chunker_version(),embedder,
                                      a.batch_size,a.reuse_from,after_batch=progress)
        report['memory']=memory_bytes()
        report['model_loads']=1 if embedder._local is not None else 0
        report['total_seconds']=report['model_load_seconds']+report['build']['elapsed_seconds']
    a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
