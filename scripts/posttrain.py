"""Collect reviewed targets or train an explicit local model; never deploy it."""
import argparse
import json
from pathlib import Path
from maintainer_copilot.training import prepare, train

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    collect=sub.add_parser('prepare')
    collect.add_argument('--db',type=Path,default=Path('data/adoption.sqlite'))
    collect.add_argument('--output',type=Path,required=True)
    run=sub.add_parser('train')
    run.add_argument('--dataset',type=Path,required=True)
    run.add_argument('--model',type=Path,required=True)
    run.add_argument('--output',type=Path,required=True)
    run.add_argument('--epochs',type=int,default=1)
    run.add_argument('--max-length',type=int,default=1024)
    args=p.parse_args()
    if args.command=='prepare':
        reserved=[]
        for path in Path('eval/datasets').glob('*.jsonl'):
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    row=json.loads(line)
                    reserved.append(row.get('question') or row.get('query') or row.get('title') or '')
        result=prepare(args.db,args.output,reserved)
    else:
        result=train(args.dataset,args.model,args.output,args.epochs,args.max_length)
    print(json.dumps(result,ensure_ascii=False,indent=2))
