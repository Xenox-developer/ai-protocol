"""Bounded-generator and global-deadline integration check on private instances."""
import asyncio
import json
from pathlib import Path
import tempfile
from analyze import analyze_run
from run import run_one


async def main():
    config = json.loads(Path(__file__).with_name('config.json').read_text())
    config.update(max_pending=1, run_timeout_s=.05)
    scenario = {'name': 'accounting_check'}
    tasks = [{'id': str(i), 'class': 'interactive', 'owner': 'demo-owner',
              'arrival_ms': 0, 'operation': 'search', 'params': {'query': str(i)}} for i in range(3)]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        await run_one(root, config, scenario, tasks, 'A', 1)
        result = analyze_run(root / 'accounting_check-r1-A')
        assert result['interactive']['tasks'] == 3
        assert result['interactive']['generator_rejections'] == 2
        assert result['interactive']['unfinished'] == 1
        assert result['final_outstanding'] == 0
        print('PASS: capacity rejections counted; run deadline counted; outstanding drained')


if __name__ == '__main__':
    asyncio.run(main())
