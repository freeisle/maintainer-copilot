"""R0 legacy / R1 rebuilt / R2 rebuilt+E3, fixed query and source hashes."""
import argparse
import asyncio
import gc
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from eval.diagnose_retrieval import run
from eval.retrieval_experiments import MemoEmbedder
from eval.score_cache import CachedReranker
from maintainer_copilot.rag.retriever import HybridRetriever
from maintainer_copilot.rag.versions import IndexVersions


async def regression(args):
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():
        raise ValueError('Commit implementation before running regression')
    out=Path(args.output);out.mkdir(exist_ok=False)
    versions=IndexVersions(os.environ['MC_TEST_DATABASE_URL'],args.registry)
    new=versions.get(args.version)
    if new['status']!='ready':raise ValueError('Rebuilt index not ready')
    old=versions.get(args.legacy)
    if old['config']['model']['revision'] is not None:raise ValueError('R0 must preserve unknown historical provenance')
    source_hash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path('src').rglob('*.py'))+sorted(Path('eval').glob('*.py')))).hexdigest()
    embedder=MemoEmbedder()
    cases=[json.loads(line) for line in Path('eval/datasets/retrieval-v2.jsonl').read_text(encoding='utf-8').splitlines()]
    for item in cases:embedder.embed_query(item['question'])
    embedder._local=None;gc.collect()
    ranker=CachedReranker(args.cache,'953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e')
    reports={}
    for name,v,experimental,snapshot in [('R0',args.legacy,False,'data/retrieval-snapshot.jsonl'),
                                       ('R1',args.version,False,args.snapshot),('R2',args.version,True,args.snapshot)]:
        record=versions.get(v)
        r=HybridRetriever(versions.scoped_url(v),embedder,ranker)
        options=SimpleNamespace(output=str(out/(name+'.json')),ids=None,no_rerank=False,
                                symbol_recall=experimental,coverage_pool=experimental,rank_blend=experimental,
                                score_cache=args.cache,reranker_revision=ranker.revision,dataset='retrieval-v2',
                                expected_rows=record['expected'],snapshot=snapshot)
        report=await run(options,retriever=r)
        report.update(index_version=v,index_metadata=record,source_implementation_sha256=source_hash)
        (out/(name+'.json')).write_text(json.dumps(report,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
        reports[name]=report
    now=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path('src').rglob('*.py'))+sorted(Path('eval').glob('*.py')))).hexdigest()
    if now!=source_hash:raise ValueError('Implementation drift')
    summary={}
    for name,r in reports.items():
        summary[name]=dict(metrics=r['metrics'],failures=sum(i['metrics']['hit@8']==0 for i in r['items']),
                           delta={k:v-reports['R0']['metrics'][k] for k,v in r['metrics'].items()},changes=[])
        for a,b in zip(reports['R0']['items'],r['items'],strict=True):
            delta={k:v-a['metrics'][k] for k,v in b['metrics'].items()}
            if any(delta.values()):summary[name]['changes'].append(dict(id=b['id'],delta=delta))
    historical=json.loads(Path('eval/reports/quality-20261026/matrix/E0.json').read_text(encoding='utf-8'))
    summary['baseline_reproduced']=all(a['retrieved']==b['retrieved'] for a,b in zip(reports['R0']['items'],historical['items'],strict=True))
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('--registry',default='mc_rebuild_20261028')
    p.add_argument('--version',default='rebuild-v2')
    p.add_argument('--legacy',default='legacy-v1')
    p.add_argument('--snapshot',default='data/rebuild-v2-result.snapshot.jsonl')
    p.add_argument('--output',required=True)
    p.add_argument('--cache',required=True)
    args=p.parse_args()
    if sys.platform=='win32':asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(regression(args))
