"""Fresh paired C/S runs; four producers, identical task partitions and credentials."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import sys

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE / 'multiclient'))
spec=importlib.util.spec_from_file_location('owner_partition',BASE / 'multiclient/run.py')
partitioning=importlib.util.module_from_spec(spec);spec.loader.exec_module(partitioning)
original=partitioning.original
from analyze_dispatcher import analyze_run, build_summary


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    config=json.loads((BASE/'stage4_1/config.json').read_text())
    config.update(process_counts=[4],agent_owner='demo-owner',assignment='round_robin',
                  interactive_background_processes=1,mode_orders=['CS','SC','CS','SC','CS','SC'])
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    original.save(root/'config.json',config)
    environment=original.metadata(config)
    environment['binary_sha256']['dispatcher']=hashlib.sha256((original.BIN/'dispatcher').read_bytes()).hexdigest()
    environment['source_sha256'].update({str(p.relative_to(original.ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in Path(__file__).parent.glob('*.py')})
    source=BASE/'results/multiclient-main'
    for scenario in config['scenarios']:
        name=scenario['name']+'-schedule.json'
        (root/name).write_bytes((source/name).read_bytes())
    original.save(root/'environment.json',environment)
    pair=0
    for scenario in config['scenarios']:
        tasks=json.loads((root/(scenario['name']+'-schedule.json')).read_text())
        for repetition in range(1,4):
            credentials={name:secrets.token_urlsafe(32) for name in (
                'AGENT_TOKEN_1','AGENT_TOKEN_2','AGENT_TOKEN_3','AGENT_TOKEN_4',
                'OTHER_AGENT_TOKEN','INTERACTIVE_TOKEN','PRODUCT_ONLY_TOKEN','ADMIN_TOKEN')}
            for mode in config['mode_orders'][pair]:
                label=f"{scenario['name']}-r{repetition}-{mode}"
                await original.run_one(root,config,scenario,tasks,mode,repetition,
                                       clients=partitioning.partition(tasks,4),owners=('demo-owner',),label=label,credentials=credentials)
                analyze_run(root/label)
            pair+=1
    runs=build_summary(root)
    print(json.dumps({'runs':len(runs),'admission_violations':sum(r['admission_violations'] for r in runs),
                      'final_outstanding':sum(r['final_outstanding'] for r in runs)}),flush=True)


if __name__=='__main__': asyncio.run(main())
