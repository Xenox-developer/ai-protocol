"""Partition one saved owner's workload across independent C/D processes."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'stage4'))
import run as original
from analyze_clients import analyze_run, build_summary


def partition(tasks, count):
    assert count in (1, 2, 4)
    assert all(task['owner'] == 'demo-owner' for task in tasks)
    agents = [task for task in tasks if task['class'] == 'agent']
    groups = [{'id': f'agent-{index + 1}', 'token_env': f'AGENT_TOKEN_{index + 1}',
               'tasks': agents[index::count]} for index in range(count)]
    groups.append({'id': 'interactive', 'token_env': 'INTERACTIVE_TOKEN',
                   'tasks': [task for task in tasks if task['class'] == 'interactive']})
    ids = [task['id'] for group in groups for task in group['tasks']]
    assert len(ids) == len(set(ids)) == len(tasks)
    assert set(ids) == {task['id'] for task in tasks}
    return groups


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Keep deadlines, arrival times, operation mix, retries, and upstream unchanged.
    config = json.loads((BASE / 'stage4_1/config.json').read_text())
    config.update(process_counts=[1, 2, 4], agent_owner='demo-owner',
                  assignment='round_robin', interactive_background_processes=1,
                  mode_orders=['CD' if pair % 2 == 0 else 'DC' for pair in range(18)])
    source = original.ROOT / config['source_results']
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    original.save(root / 'config.json', config)
    environment = original.metadata(config)
    environment['source_sha256'].update({str(p.relative_to(original.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in Path(__file__).parent.glob('*.py')})
    environment['source_schedule_sha256'] = {}
    for scenario in config['scenarios']:
        filename = scenario['name'] + '-schedule.json'
        raw = (source / filename).read_bytes()
        environment['source_schedule_sha256'][filename] = hashlib.sha256(raw).hexdigest()
        tasks = [task for task in json.loads(raw) if task['owner'] == 'demo-owner']
        original.save(root / filename, tasks)
    original.save(root / 'environment.json', environment)
    pair = 0
    for scenario in config['scenarios']:
        tasks = json.loads((root / (scenario['name'] + '-schedule.json')).read_text())
        for repetition in range(1, config['repetitions'] + 1):
            # Rotate process counts to reduce confounding with machine/time drift.
            counts = config['process_counts']
            shift = repetition - 1
            for count in counts[shift:] + counts[:shift]:
                for mode in config['mode_orders'][pair]:
                    label = f"{scenario['name']}-n{count}-r{repetition}-{mode}"
                    await original.run_one(root, config, scenario, tasks, mode, repetition,
                                           clients=partition(tasks, count), owners=('demo-owner',), label=label)
                    analyze_run(root / label)
                pair += 1
    runs = build_summary(root)
    print(json.dumps({'runs': len(runs), 'admission_violations': sum(r['admission_violations'] for r in runs),
                      'final_outstanding': sum(r['final_outstanding'] for r in runs)}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
