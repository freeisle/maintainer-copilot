"""Read-only source/cohort audit; never treat planned chunks as indexed vectors."""
import argparse
import collections
import json
import subprocess
import sys
import types
from pathlib import Path

from scripts.rebuild_index import sources
from maintainer_copilot.rag.indexer import chunk_id
from maintainer_copilot.rag.versions import digest


def audit():
    docs,manifest=sources()
    old={r['id']:r for r in map(json.loads,Path('data/retrieval-snapshot.jsonl').read_text(encoding='utf-8').splitlines())}
    rows=[dict(id=chunk_id(d.repo,c),repo=d.repo,path=d.path,source=c.source,text=c.text,meta=c.meta) for d in docs for c in d.chunks]
    new={r['id']:r for r in rows}
    # Reconstruct legacy cutting using the same repository's recorded source ref.
    ref=manifest['local/maintainer-copilot']
    code=subprocess.check_output(['git','show',ref+':src/maintainer_copilot/rag/chunkers.py']).decode('utf-8')
    legacy=types.ModuleType('_index_audit_legacy_chunkers')
    sys.modules[legacy.__name__]=legacy
    exec(compile(code,'frozen legacy chunker','exec'),legacy.__dict__)
    regenerated={}
    for d in docs:
        chunks=(legacy.CodeChunker() if d.source=='code' else legacy.DocChunker()).split(d.text,d.path) if d.source!='issue' else d.chunks
        for c in chunks:
            regenerated[chunk_id(d.repo,c)]=dict(repo=d.repo,path=d.path,source=c.source,text=c.text,meta=c.meta)
    def identity(r):
        return r['repo'],('issue#'+str(r['meta']['issue']) if r['source']=='issue' else r['path']),digest(r['text'])
    previous=collections.Counter(map(identity,old.values()))
    frozen=collections.Counter(map(identity,regenerated.values()))
    only_frozen,only_previous=frozen-previous,previous-frozen
    changes=[]
    for cid in sorted(new.keys()|old.keys()):
        a,b=old.get(cid),new.get(cid)
        if not a or not b or a['text']!=b['text']:
            r=b or a
            changes.append(dict(id=cid,repo=r['repo'],path=r['path'],kind='added' if not a else 'removed' if not b else 'changed',new_kind=(b or {}).get('meta',{}).get('kind')))
    cases=[json.loads(line) for line in Path('eval/datasets/retrieval-v2.jsonl').read_text(encoding='utf-8').splitlines()]
    evidence=[]
    for q in cases:
        if q['id'] not in ('ret-004','ret-007','ret-014'):continue
        evidence.append(dict(id=q['id'],targets=[dict(path=p,planned_chunks=sum(r['repo']==q['repo'] and r['path']==p for r in rows)) for p in q['relevant']]))
    return dict(source_revisions=manifest,source_documents=len(docs),planned_chunks=len(rows),
                empty_chunks=sum(not r['text'].strip() for r in rows),identity_duplicates=len(rows)-len(new),
                per_repo=[dict(repo=repo,documents=sum(d.repo==repo for d in docs),chunks=sum(r['repo']==repo for r in rows)) for repo in sorted(manifest)],
                differences=dict(collections.Counter(c['kind'] for c in changes)),changes=changes,
                added_kinds=dict(collections.Counter(c['new_kind'] or 'fallback' for c in changes if c['kind']=='added')),
                legacy_source_reconstruction=dict(chunker_ref=ref,rows=len(regenerated),
                    content_multiset_equal=previous==frozen,
                    only_frozen_source=sum(only_frozen.values()),only_original=sum(only_previous.values()),
                    differing_source_paths=sorted({identity(r)[:2] for r in regenerated.values() if identity(r) in only_frozen})),
                target_source_coverage=evidence,
                note='Planned/source-side evidence only. Fixed Git refs do not prove historical working trees were clean; baseline has six different local retriever fragments. Full new vectors and quality comparison require completed generation.')


if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__);p.add_argument('--output',required=True)
    a=p.parse_args()
    with Path(a.output).open('x',encoding='utf-8') as f:json.dump(audit(),f,ensure_ascii=False,indent=2)
