"""Stage-4 metrics plus policy traffic, application delay, and explicit phase cohorts."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'stage4'))
import analyze as original


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def failure_reasons(records):
    return {kind: dict(Counter(r['reason'] for r in records if r['class'] == kind and r['outcome'] == 'failed'))
            for kind in ('interactive', 'agent')}


def application_delays(events, changes):
    result = []
    for change in changes:
        snapshot = change['response']
        applied = next((e['at_ms'] for e in events if e['event'] == 'policy' and e['owner'] == snapshot['principal_id']
                        and e['ready'] and e['revision'] == snapshot['policy_revision']
                        and e['limit'] == snapshot['limits']['max_outstanding']), None)
        result.append({'owner': snapshot['principal_id'], 'revision': snapshot['policy_revision'],
                       'limit': snapshot['limits']['max_outstanding'], 'applied_ms': applied,
                       'change_request_ms': change['request_started_s'] * 1000,
                       'change_response_ms': change['actual_s'] * 1000,
                       # Server mutation lies within the admin request/response interval.
                       'delay_lower_ms': max(0, applied - change['actual_s'] * 1000) if applied is not None else None,
                       'delay_upper_ms': max(0, applied - change['request_started_s'] * 1000) if applied is not None else None})
    return result


def phase_results(records, events, upstream, start_unix_s, dynamic):
    windows = [('before_start', float('-inf'), 0)]
    windows += [('initial', 0, 2000), ('reduced', 2000, 5000), ('restored', 5000, float('inf'))] if dynamic else [('constant', 0, float('inf'))]
    output = {}
    for name, lo, hi in windows:
        cohort = [r for r in records if lo <= r['arrival_ms'] < hi]
        ended = [e for e in events if e['event'] == 'attempt_end' and lo <= e['at_ms'] < hi]
        output[name] = {
            'arrival_cohort': original.summarize(cohort), 'cohort_failure_reasons': failure_reasons(cohort),
            'activity': {
                'operation_starts': sum(e['event'] == 'attempt_start' and lo <= e['at_ms'] < hi for e in events),
                'http_429_responses': sum(e.get('status') == 429 for e in ended),
                'queue_timeout_responses': sum(e.get('status') == 503 and e.get('error') == {'code':'queue_timeout','execution':'not_started'} for e in ended),
                'discovery_starts': sum(e['event'] == 'discovery_start' and lo <= e['at_ms'] < hi for e in events),
                'terminal_outcomes': dict(Counter(e['outcome'] for e in events if e['event'] == 'terminal' and lo <= e['at_ms'] < hi)),
                'upstream_starts': sum(e['event'] == 'start' and lo <= (e['unix_s'] - start_unix_s) * 1000 < hi
                                       for e in upstream if not e['key'].startswith('warmup')),
            },
        }
    return output


def analyze_run(path):
    summary = original.analyze_run(path)
    config = json.loads((path / 'client.json').read_text())
    records = original.read_jsonl(path / 'tasks.jsonl')
    events = original.read_jsonl(path / 'client-events.jsonl')
    changes = original.read_jsonl(path / 'admin.jsonl')
    upstream = original.read_jsonl(path / 'upstream.jsonl')
    started = [e for e in events if e['event'] == 'discovery_start']
    ended = [e for e in events if e['event'] == 'discovery_end']
    finals = [e for e in events if e['event'] == 'gate_final']
    assert len(finals) == 2 and all(e['active'] == 0 for e in finals)
    delays = application_delays(events, changes)
    for owner in ('demo-owner', 'other-owner'):
        policies = [e for e in events if e['event'] == 'policy' and e['owner'] == owner and e['ready']]
        assert policies and policies[0]['limit'] == 5 and policies[0]['revision'] == 1
        final = next(e for e in finals if e['owner'] == owner)
        if config['mode'] == 'D':
            assert len(policies) == 1, 'Fixed client must stop after the initial valid policy'
            assert final['limit'] == 5 and final['revision'] == 1
        elif changes:
            assert final['limit'] == 5 and final['revision'] == 3
    assert all((d['applied_ms'] is None) == (config['mode'] == 'D') for d in delays)
    summary.update(discovery={
        'client_requests': len(started), 'client_successes': sum(e['success'] for e in ended),
        'client_failures': sum(not e['success'] for e in ended), 'client_incomplete': len(started) - len(ended),
        'by_owner': dict(Counter(e['owner'] for e in started)),
        'harness_requests': json.loads((path / 'cleanup.json').read_text())['harness_discovery'],
    }, application_delays=delays, gate_final=finals, terminal_failure_reasons=failure_reasons(records),
       phases=phase_results(records, events, upstream, config['start_unix_s'], bool(changes)))
    write(path / 'summary.json', summary)
    return summary


def build_comparison(root):
    runs = [analyze_run(path.parent) for path in sorted(root.glob('*/client.json')) if (path.parent / 'cleanup.json').exists()]
    expected = {(scenario, mode, repetition) for scenario in ('mixed_overload', 'dynamic') for mode in 'CD' for repetition in (1,2,3)}
    assert {(r['scenario'],r['mode'],r['repetition']) for r in runs} == expected, 'Incomplete comparison series'
    write(root / 'summary.json', runs)
    write(root / 'analysis-version.json', {str(p.name): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in (Path(__file__), Path(original.__file__))})
    pairs = []
    for scenario in ('mixed_overload', 'dynamic'):
        for repetition in (1, 2, 3):
            pair = {mode: next(r for r in runs if r['scenario'] == scenario and r['repetition'] == repetition and r['mode'] == mode) for mode in 'CD'}
            for kind in ('interactive', 'agent'):
                row = {'scenario':scenario, 'repetition':repetition, 'class':kind, 'difference':'C minus D'}
                for key in ('success_fraction', 'successful_e2e_p95_ms', 'attempts_per_task', 'http_429'):
                    c,d=pair['C'][kind][key],pair['D'][kind][key]
                    row[key] = c-d if c is not None and d is not None else None
                row['discovery_requests'] = pair['C']['discovery']['client_requests'] - pair['D']['discovery']['client_requests']
                pairs.append(row)
    write(root / 'paired-differences.json', pairs)
    def show(v): return 'n/a' if v is None else f'{v:.2f}'
    lines = ['# Stage 4.1 per-run summary', '',
        'Percentiles cover successful logical tasks only, from scheduled arrival. Each row is one run.',
        'Discovery counts are client policy requests; harness probes are separate in JSON.', '',
        '| Scenario | Rep | Mode | Class | Success / total | Failed | Unfinished | p50 / p95 / p99 ms | Attempts/task | 429 | Discovery* | Actual upstream* |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for run in runs:
        for kind in ('interactive','agent'):
            row=run[kind]
            latency=' / '.join(show(row['successful_e2e_'+p+'_ms']) for p in ('p50','p95','p99'))
            lines.append(f"| {run['scenario']} | {run['repetition']} | {run['mode']} | {kind} | {row['success']}/{row['tasks']} | {row['failed']} | {row['unfinished']} | {latency} | {row['attempts_per_task']:.3f} | {row['http_429']} | {run['discovery']['client_requests']} | {run['upstream_started']} |")
    lines += ['', '*Run-wide values repeated across class rows; do not sum these two rows.', '',
              '## Policy application delay', '',
              'Mutation occurs inside the recorded admin request/response interval. Bounds below use the client application log timestamp.', '',
              '| Rep | Mode | Owner | Limit | Delay lower..upper (ms) |', '|---|---|---|---|---|']
    for run in runs:
        for delay in run['application_delays']:
            value=f"{show(delay['delay_lower_ms'])}..{show(delay['delay_upper_ms'])}" if delay['applied_ms'] is not None else 'not applied (fixed initial policy)'
            lines.append(f"| {run['repetition']} | {run['mode']} | {delay['owner']} | {delay['limit']} | {value} |")
    lines += ['', '## Phase interpretation', '',
        'summary.json includes both full outcomes for arrival cohorts and events occurring in each time window.',
        'Dynamic windows: initial [0,2s), reduced [2,5s), restored [5s,end). Actual admin timing is retained separately.',
        'A reduced-window arrival can finish after recovery; its full latency and outcome stay in the reduced arrival cohort.',
        'Terminal failure reasons, queue/budget snapshots, discovery failures and interrupted requests remain in raw/derived JSON.',
        f"Incomplete final diagnostic samples: {sum(r['incomplete_final_trace_samples'] for r in runs)}."]
    (root/'SUMMARY.md').write_text('\n'.join(lines)+'\n')
    return runs


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('results', type=Path)
    build_comparison(parser.parse_args().results)
