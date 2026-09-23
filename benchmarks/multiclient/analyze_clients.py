"""Audit independent process partitions, shared admissions, and per-client outcomes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'stage4_1'))
import comparison
original = comparison.original
write = comparison.write


def validate_partition(tasks, manifest, events):
    assignments = {task_id: client['id'] for client in manifest for task_id in client['tasks']}
    assert len(assignments) == sum(len(client['tasks']) for client in manifest) == len(tasks)
    assert set(assignments) == {task['id'] for task in tasks}
    assert len({client['pid'] for client in manifest}) == len(manifest)
    assert len({client['token_env'] for client in manifest}) == len(manifest)
    for event in events:
        if 'id' in event:
            assert assignments[event['id']] == event['client_id'], 'Task moved between clients'
    arrivals = Counter(e['id'] for e in events if e['event'] == 'arrival')
    terminals = Counter(e['id'] for e in events if e['event'] == 'terminal')
    assert arrivals == terminals == Counter(assignments.keys()), 'Missing/duplicate task events'
    return assignments


def client_policy(events, changes, mode):
    discoveries = [e for e in events if e['event'] == 'discovery_start']
    ended = [e for e in events if e['event'] == 'discovery_end']
    assert all(e['owner'] == 'demo-owner' for e in discoveries)
    finals = [e for e in events if e['event'] == 'gate_final']
    assert all(e['active'] == 0 for e in finals)
    policies = [e for e in events if e['event'] == 'policy' and e['ready']]
    assert policies and policies[0]['limit'] == 5 and policies[0]['revision'] == 1
    final = next(e for e in finals if e['owner'] == 'demo-owner')
    delays = comparison.application_delays(events, changes)
    if mode == 'D':
        assert len(policies) == len(discoveries) == 1
        assert (final['limit'], final['revision']) == (5, 1)
        assert all(d['applied_ms'] is None for d in delays)
    else:
        assert all(d['applied_ms'] is not None for d in delays)
        assert (final['limit'], final['revision']) == (5, 3 if changes else 1)
    return {'requests': len(discoveries), 'successes': sum(e['success'] for e in ended),
            'failures': sum(not e['success'] for e in ended), 'incomplete': len(discoveries) - len(ended),
            'policies': policies, 'application_delays': delays, 'final': final}


def analyze_run(path):
    summary = original.analyze_run(path)
    config = json.loads((path / 'client.json').read_text())
    manifest = json.loads((path / 'processes.json').read_text())
    events = original.read_jsonl(path / 'client-events.jsonl')
    assignments = validate_partition(config['tasks'], manifest, events)
    records = original.read_jsonl(path / 'tasks.jsonl')
    changes = original.read_jsonl(path / 'admin.jsonl')
    upstream = original.read_jsonl(path / 'upstream.jsonl')
    clients = []
    for client in manifest:
        selected = [e for e in events if e['client_id'] == client['id']]
        rows = [r for r in records if assignments[r['id']] == client['id']]
        actual_config = json.loads((path / f"{client['id']}.json").read_text())
        expected_tasks = [t for t in config['tasks'] if assignments[t['id']] == client['id']]
        assert actual_config['tasks'] == expected_tasks
        for key in ('mode', 'start_unix_s', 'task_timeout_s', 'run_timeout_s', 'max_pending'):
            assert actual_config[key] == config[key]
        agent = client['id'] != 'interactive'
        kind = 'agent' if agent else 'interactive'
        result = {'id': client['id'], 'pid': client['pid'], 'class': kind,
                  **original.summarize(rows)[kind],
                  'failure_reasons': comparison.failure_reasons(rows)[kind]}
        result['completion_share'] = result['success'] / summary[kind]['success'] if summary[kind]['success'] else None
        if agent:
            result['discovery'] = client_policy(selected, changes, config['mode'])
        else:
            assert not any(e['event'] == 'discovery_start' for e in selected)
            result['discovery'] = {'requests': 0}
        clients.append(result)
    # Reuse the real retry implementation; also audit its externally observed bound.
    for record in records:
        assert len(record['attempts']) <= 5
        for before, after in zip(record['attempts'], record['attempts'][1:]):
            assert before.get('status') == 429 or (before.get('status') == 503 and before.get('error') == {'code': 'queue_timeout', 'execution': 'not_started'})
            assert after['start_ms'] - before['end_ms'] >= int(before['retry_after'] or 1) * 1000
    agents = [c for c in clients if c['class'] == 'agent']
    summary.update(process_count=len(agents), clients=clients,
                   discovery_requests=sum(c['discovery']['requests'] for c in clients),
                   harness_discovery=json.loads((path / 'cleanup.json').read_text())['harness_discovery'],
                   terminal_failure_reasons=comparison.failure_reasons(records),
                   phases=comparison.phase_results(records, events, upstream, config['start_unix_s'], bool(changes)))
    assert sum(c['attempts'] for c in clients) == sum(summary[k]['attempts'] for k in ('agent', 'interactive'))
    assert all(c['generator_rejections'] == c['unfinished'] == 0 for c in clients)
    write(path / 'summary.json', summary)
    return summary


def build_summary(root):
    runs = [analyze_run(p.parent) for p in sorted(root.glob('*/processes.json'))]
    expected = {(s, n, r, m) for s in ('mixed_overload', 'dynamic') for n in (1, 2, 4) for r in (1, 2, 3) for m in 'CD'}
    assert {(r['scenario'], r['process_count'], r['repetition'], r['mode']) for r in runs} == expected
    # Every run must retain exactly the same task objects and planned arrivals.
    for path in root.glob('*/client.json'):
        config = json.loads(path.read_text())
        assert config['tasks'] == json.loads((root / (config['scenario'] + '-schedule.json')).read_text())
    write(root / 'summary.json', runs)
    write(root / 'analysis-version.json', {str(p.relative_to(BASE)): hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in (Path(__file__), Path(original.__file__), Path(comparison.__file__))})
    totals = []
    for scenario in ('mixed_overload', 'dynamic'):
        for count in (1, 2, 4):
            for mode in 'CD':
                group = [r for r in runs if (r['scenario'], r['process_count'], r['mode']) == (scenario, count, mode)]
                row = {'scenario': scenario, 'process_count': count, 'mode': mode,
                       'median_run_p95_ms': statistics.median(r['agent']['successful_e2e_p95_ms'] for r in group),
                       'failure_reasons': dict(sum((Counter(r['terminal_failure_reasons']['agent']) for r in group), Counter())),
                       'discovery_requests': sum(r['discovery_requests'] for r in group),
                       'upstream_started': sum(r['upstream_started'] for r in group),
                       'client_completed_by_run': [[c['success'] for c in r['clients'] if c['class'] == 'agent'] for r in group]}
                row.update({key: sum(r['agent'][key] for r in group) for key in ('tasks', 'success', 'failed', 'attempts', 'http_429')})
                totals.append(row)
    write(root / 'totals.json', totals)
    pairs = []
    for c in runs:
        if c['mode'] != 'C':
            continue
        d = next(r for r in runs if r['mode'] == 'D' and all(r[key] == c[key] for key in ('scenario', 'process_count', 'repetition')))
        pairs.append({**{key: c[key] for key in ('scenario', 'process_count', 'repetition')},
                      'difference': 'C minus D',
                      **{key: c['agent'][key] - d['agent'][key] for key in ('success', 'http_429', 'attempts', 'successful_e2e_p95_ms')},
                      'discovery_requests': c['discovery_requests'] - d['discovery_requests']})
    write(root / 'paired-differences.json', pairs)
    lines = ['# Independent-client experiment: per-run results', '',
             'Agent metrics only; full interactive and per-process metrics are in summary.json.',
             'Latency includes local waiting and retries from planned arrival. Percentiles cover successful tasks only.', '',
             '| Scenario | Processes | Rep | Mode | Success/total | p50 / p95 / p99 ms | Working requests | 429 | Discovery | Successful completions by client |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for run in runs:
        a = run['agent']
        latency = ' / '.join(f"{a['successful_e2e_'+p+'_ms']:.1f}" for p in ('p50', 'p95', 'p99'))
        completions = ', '.join(str(c['success']) for c in run['clients'] if c['class'] == 'agent')
        lines.append(f"| {run['scenario']} | {run['process_count']} | {run['repetition']} | {run['mode']} | {a['success']}/{a['tasks']} | {latency} | {a['attempts']} | {a['http_429']} | {run['discovery_requests']} | {completions} |")
    (root / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    return runs


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('results', type=Path)
    build_summary(parser.parse_args().results)
