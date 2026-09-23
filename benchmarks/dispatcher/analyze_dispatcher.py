"""Compare independent C with shared S using the same logical-task reconstruction."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'multiclient'))
import analyze_clients as independent
original = independent.original
comparison = independent.comparison
write = comparison.write


def analyze_run(path):
    config = json.loads((path / 'client.json').read_text())
    if config['mode'] == 'C':
        summary = independent.analyze_run(path)
        summary['identity_discovery_requests'] = 0
        return summary
    summary = original.analyze_run(path)
    events = original.read_jsonl(path / 'client-events.jsonl')
    manifest = json.loads((path / 'processes.json').read_text())
    assignments = independent.validate_partition(config['tasks'], manifest, events)
    records = original.read_jsonl(path / 'tasks.jsonl')
    dispatch = [e for e in events if e.get('executor') == 'dispatcher']
    assert not any(e['event'] == 'discovery_start' for e in events if e.get('executor') != 'dispatcher')
    policies = [e for e in dispatch if e['event'] == 'policy' and e['ready']]
    assert policies and policies[0]['limit'] == 5 and policies[0]['revision'] == 1
    changes = original.read_jsonl(path / 'admin.jsonl')
    delays = comparison.application_delays(policies, changes)
    assert all(d['applied_ms'] is not None for d in delays)
    finals = [e for e in dispatch if e['event'] == 'gate_final']
    assert len(finals) == 1 and finals[0]['active'] == 0
    assert finals[0]['limit'] == 5 and finals[0]['revision'] == (3 if changes else 1)
    discovery = [e for e in dispatch if e['event'] == 'discovery_start']
    ended = [e for e in dispatch if e['event'] == 'discovery_end']
    assert len(discovery) == len(ended) and all(e['success'] for e in ended)
    identity = [e for e in discovery if e['purpose'] == 'identity']
    assert len(identity) == 4 and len({e['credential_index'] for e in identity}) == 4
    assert len({p['owner'] for p in policies}) == 1
    for row in records:
        assert len(row['attempts']) <= 5
        for a, b in zip(row['attempts'], row['attempts'][1:]):
            assert a.get('status') == 429 or (a.get('status') == 503 and a.get('error') == {'code':'queue_timeout','execution':'not_started'})
            assert b['start_ms'] - a['end_ms'] >= int(a['retry_after'] or 1) * 1000
    clients = []
    for client in manifest:
        assigned = [t for t in config['tasks'] if assignments[t['id']] == client['id']]
        actual = json.loads((path / f"{client['id']}.json").read_text())
        assert actual['tasks'] == assigned
        assert all(actual[k] == config[k] for k in ('mode','start_unix_s','task_timeout_s','run_timeout_s','max_pending'))
        kind = 'interactive' if client['id'] == 'interactive' else 'agent'
        clients.append({'id':client['id'], 'class':kind,
                        **original.summarize([r for r in records if assignments[r['id']] == client['id']])[kind]})
    summary.update(process_count=4, clients=clients, discovery_requests=len(discovery), identity_discovery_requests=len(identity),
                   application_delays=delays, gate_final=finals,
                   harness_discovery=json.loads((path / 'cleanup.json').read_text())['harness_discovery'],
                   terminal_failure_reasons=comparison.failure_reasons(records),
                   phases=comparison.phase_results(records, events, original.read_jsonl(path / 'upstream.jsonl'), config['start_unix_s'], bool(changes)))
    assert all(summary[k]['unfinished'] == summary[k]['generator_rejections'] == 0 for k in ('agent','interactive'))
    write(path / 'summary.json', summary)
    return summary


def build_summary(root):
    paths = sorted(root.glob('*/processes.json'))
    runs = [analyze_run(p.parent) for p in paths]
    assert {(r['scenario'],r['repetition'],r['mode']) for r in runs} == {
        (s,r,m) for s in ('mixed_overload','dynamic') for r in (1,2,3) for m in 'CS'}
    for p in paths:
        config = json.loads((p.parent / 'client.json').read_text())
        assert config['tasks'] == json.loads((root / (config['scenario'] + '-schedule.json')).read_text())
    write(root / 'summary.json', runs)
    totals = []
    for scenario in ('mixed_overload','dynamic'):
        for mode in 'CS':
            group = [r for r in runs if r['scenario'] == scenario and r['mode'] == mode]
            totals.append({'scenario':scenario,'mode':mode,
                           **{k:sum(r['agent'][k] for r in group) for k in ('tasks','success','failed','unfinished','attempts','http_429')},
                           'median_run_p95_ms':statistics.median(r['agent']['successful_e2e_p95_ms'] for r in group),
                           'discovery_requests':sum(r['discovery_requests'] for r in group),
                           'identity_discovery_requests':sum(r['identity_discovery_requests'] for r in group),
                           'upstream_started':sum(r['upstream_started'] for r in group),
                           'failure_reasons':dict(sum((Counter(r['terminal_failure_reasons']['agent']) for r in group),Counter()))})
    write(root / 'totals.json', totals)
    pairs = []
    for c in runs:
        if c['mode'] != 'C': continue
        s = next(r for r in runs if (r['scenario'],r['repetition'],r['mode']) == (c['scenario'],c['repetition'],'S'))
        pairs.append({'scenario':c['scenario'],'repetition':c['repetition'],'difference':'S minus C',
                      **{k:s['agent'][k]-c['agent'][k] for k in ('success','attempts','http_429','successful_e2e_p95_ms')},
                      'discovery_requests':s['discovery_requests']-c['discovery_requests']})
    write(root / 'paired-differences.json', pairs)
    write(root / 'analysis-version.json', {str(p.relative_to(BASE)):hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in (Path(__file__),Path(independent.__file__),Path(original.__file__),Path(comparison.__file__))})
    lines = ['# Four agent processes: independent C versus shared dispatcher S', '',
             'Latency includes all waiting from planned arrival; percentiles cover successful tasks only.',
             'Discovery includes dispatcher identity verification; harness probes are separate.', '',
             '| Scenario | Rep | Mode | Class | Success/total | p50 / p95 / p99 ms | Working requests | 429 | Discovery* |',
             '|---|---|---|---|---|---|---|---|---|']
    for run in runs:
        for kind in ('agent','interactive'):
            r=run[kind];latency=' / '.join(f"{r['successful_e2e_'+p+'_ms']:.1f}" for p in ('p50','p95','p99'))
            lines.append(f"| {run['scenario']} | {run['repetition']} | {run['mode']} | {kind} | {r['success']}/{r['tasks']} | {latency} | {r['attempts']} | {r['http_429']} | {run['discovery_requests']} |")
    lines += ['', '*Run-wide discovery repeated across class rows; do not sum the two rows.']
    (root / 'SUMMARY.md').write_text('\n'.join(lines)+'\n')
    return runs


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('results',type=Path)
    build_summary(parser.parse_args().results)
