"""Fresh paired C/D comparison using the unchanged stage-4 traffic and upstream."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'stage4'))
import run as original
from comparison import build_comparison


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    source = original.ROOT / config['source_results']
    baseline = json.loads((source / 'config.json').read_text())
    for key in ('seed', 'repetitions', 'upstream_delay_ms', 'interactive_max_wait_ms',
                'agent_max_wait_ms', 'task_timeout_s', 'run_timeout_s',
                'cleanup_timeout_s', 'max_pending', 'sample_ms'):
        assert config[key] == baseline[key], f'Changed baseline setting: {key}'
    assert config['repetitions'] >= 3
    assert [s['name'] for s in config['scenarios']] == ['mixed_overload', 'dynamic']
    assert config['mode_orders'] == ['CD', 'DC', 'CD', 'DC', 'CD', 'DC']
    for scenario in config['scenarios']:
        assert scenario in baseline['scenarios']
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    original.save(root / 'config.json', config)
    environment = original.metadata(config)
    environment['source_sha256'].update({str(p.relative_to(original.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in Path(__file__).parent.iterdir() if p.suffix in ('.py', '.json')})
    environment['schedule_sha256'] = {}
    for scenario in config['scenarios']:
        name = scenario['name'] + '-schedule.json'
        shutil.copyfile(source / name, root / name)
        environment['schedule_sha256'][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    original.save(root / 'environment.json', environment)
    pair = 0
    for scenario in config['scenarios']:
        tasks = json.loads((root / (scenario['name'] + '-schedule.json')).read_text())
        for repetition in range(1, config['repetitions'] + 1):
            for mode in config['mode_orders'][pair]:
                await original.run_one(root, config, scenario, tasks, mode, repetition)
            pair += 1
    runs = build_comparison(root)
    print(json.dumps({'runs': len(runs), 'admission_violations': sum(r['admission_violations'] for r in runs),
                      'final_outstanding': sum(r['final_outstanding'] for r in runs)}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
