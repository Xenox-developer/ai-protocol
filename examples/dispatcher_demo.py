"""Private real-gateway permission demonstration; no LLM or user servers."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'benchmarks/stage4'))
from run import run_one
from analyze import analyze_run, read_jsonl


async def demo(root):
    config=json.loads((ROOT/'benchmarks/stage4_1/config.json').read_text())
    tasks=[];groups=[]
    for index in range(4):
        group=[]
        operations=['search','product'] if index==3 else ['search']
        for operation in operations:
            task=dict(id=f'agent-{index+1}-{operation}',class_='agent',owner='demo-owner',arrival_ms=len(tasks)*5,
                      operation=operation,params={'id':12} if operation=='product' else {'query':f'agent-{index+1}-{operation}'})
            task['class']=task.pop('class_');tasks.append(task);group.append(task)
        groups.append({'id':f'agent-{index+1}','token_env':'PRODUCT_ONLY_TOKEN' if index==3 else f'AGENT_TOKEN_{index+1}','tasks':group})
    task=dict(id='interactive',owner='demo-owner',arrival_ms=30,operation='search',params={'query':'interactive'})
    task['class']='interactive';tasks.append(task)
    groups.append({'id':'interactive','token_env':'INTERACTIVE_TOKEN','tasks':[task]})
    await run_one(root,config,{'name':'permissions'},tasks,'S',1,clients=groups,owners=('demo-owner',))
    path=root/'permissions-r1-S';summary=analyze_run(path)
    rows=read_jsonl(path/'tasks.jsonl')
    restricted=next(r for r in rows if r['id']=='agent-4-search')
    assert restricted['outcome']=='failed' and len(restricted['attempts'])==1 and restricted['attempts'][0]['status']==403
    assert next(r for r in rows if r['id']=='agent-4-product')['outcome']=='success'
    assert not any(e['key']=='agent-4-search' for e in read_jsonl(path/'upstream.jsonl'))
    assert summary['upstream_started']==summary['upstream_completed']==5 and summary['final_outstanding']==0
    result={'passed':True,'tasks':6,'success':5,'restricted_search_status':403,'restricted_product':'success',
            'forbidden_search_upstream_calls':0,'final_outstanding':0}
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True,exist_ok=False);asyncio.run(demo(args.output.resolve()))
    else:
        with tempfile.TemporaryDirectory(prefix='dispatcher-demo-') as directory: asyncio.run(demo(Path(directory)))
