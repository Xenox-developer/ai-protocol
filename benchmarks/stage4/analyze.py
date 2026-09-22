"""Rebuild task records, per-run metrics, and explicitly labelled comparisons."""
import argparse
from collections import Counter
import csv
import json
import hashlib
import math
from pathlib import Path
import statistics


def read_jsonl(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]



def read_trace(path):
    """SIGTERM may interrupt the final diagnostic sample, never hide interior damage."""
    lines = path.read_text().splitlines(keepends=True)
    events = []
    incomplete = 0
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # Only a trailing, unterminated sample is optional diagnostics.
            # Admission events and all client/upstream evidence remain strict.
            if index != len(lines) - 1 or line.endswith('\n') or not line.startswith('{"active":'):
                raise
            incomplete = 1
    return events, incomplete


def percentile(values, fraction):
    """Nearest-rank percentile; no observations means no reported latency."""
    return sorted(values)[math.ceil(len(values) * fraction) - 1] if values else None


def reconstruct(tasks, events):
    records = {task['id']: {**task, 'attempts': [], 'outcome': 'unfinished',
                           'reason': 'no_terminal_event', 'completed_ms': None,
                           'arrived_ms': None} for task in tasks}
    for event in events:
        if event.get('id') not in records:
            continue
        row = records[event['id']]
        if event['event'] == 'arrival':
            row['arrived_ms'] = event['at_ms']
        elif event['event'] == 'attempt_start':
            row['attempts'].append({'number': event['number'], 'start_ms': event['at_ms'],
                                    'end_ms': None, 'result': 'incomplete'})
        elif event['event'] == 'attempt_end':
            attempt = next(a for a in row['attempts'] if a['number'] == event['number'])
            attempt.update(end_ms=event['at_ms'], status=event.get('status'),
                           error=event.get('error'), error_kind=event.get('error_kind'),
                           retry_after=event.get('retry_after'), result='response' if 'status' in event else 'network_error')
        elif event['event'] == 'terminal':
            row.update(outcome=event['outcome'], reason=event['reason'],
                       completed_ms=event['at_ms'] if event['outcome'] != 'unfinished' else None,
                       observed_end_ms=event['at_ms'])
    for row in records.values():
        row['e2e_ms'] = None if row['completed_ms'] is None else row['completed_ms'] - row['arrival_ms']
    return list(records.values())


def summarize(records):
    result = {}
    for kind in ('interactive', 'agent'):
        rows = [r for r in records if r['class'] == kind]
        counts = Counter(row['outcome'] for row in rows)
        times = [r['e2e_ms'] for r in rows if r['outcome'] == 'success']
        attempts = [a for r in rows for a in r['attempts']]
        result[kind] = {
            'tasks': len(rows), **{outcome: counts[outcome] for outcome in ('success', 'failed', 'unfinished')},
            **{outcome + '_fraction': counts[outcome] / len(rows) if rows else None for outcome in ('success', 'failed', 'unfinished')},
            'successful_e2e_p50_ms': percentile(times, .50), 'successful_e2e_p95_ms': percentile(times, .95),
            'successful_e2e_p99_ms': percentile(times, .99),
            'attempts': len(attempts), 'attempts_per_task': len(attempts) / len(rows) if rows else None,
            'http_429': sum(a.get('status') == 429 for a in attempts),
            'queue_timeout': sum(a.get('status') == 503 and a.get('error') == {'code': 'queue_timeout', 'execution': 'not_started'} for a in attempts),
            'network_errors': sum(a.get('error_kind') == 'network_error' for a in attempts),
            'upstream_errors': sum(a.get('status') in (502, 504) for a in attempts),
            'generator_rejections': sum(r['reason'] == 'generator_capacity' for r in rows),
            'arrival_lag_p99_ms': percentile([r['arrived_ms'] - r['arrival_ms'] for r in rows if r['arrived_ms'] is not None], .99),
            # Do not present an incomplete batch as a quickly completed one.
            'all_success_batch_ms': max(r['completed_ms'] for r in rows) - min(r['arrival_ms'] for r in rows)
                if rows and counts['success'] == len(rows) else None,
        }
    return result


def analyze_run(path):
    config = json.loads((path / 'client.json').read_text())
    records = reconstruct(config['tasks'], read_jsonl(path / 'client-events.jsonl'))
    with (path / 'tasks.jsonl').open('w') as stream:
        for row in records:
            stream.write(json.dumps(row) + '\n')
    summary = summarize(records)
    trace, incomplete_tail = read_trace(path / 'server.jsonl')
    admissions = [e for e in trace if e['event'] == 'admission']
    samples = [e for e in trace if e['event'] == 'sample']
    invalid = [e for e in admissions if not 0 < e['outstanding'] <= e['limit']]
    upstream = read_jsonl(path / 'upstream.jsonl')
    measured = [e for e in upstream if not e['key'].startswith('warmup')]
    summary.update(mode=config['mode'], scenario=config['scenario'], repetition=config['repetition'],
                   upstream_started=sum(e['event'] == 'start' for e in measured),
                   upstream_completed=sum(e['event'] == 'end' for e in measured),
                   admission_violations=len(invalid), incomplete_final_trace_samples=incomplete_tail,
                   maximum_active=max(e['active'] for e in samples),
                   maximum_queue_interactive=max(e['queue_interactive'] for e in samples),
                   maximum_queue_agent=max(e['queue_agent'] for e in samples),
                   final_outstanding=json.loads((path / 'cleanup.json').read_text())['outstanding'])
    assert not invalid, 'A new admission exceeded its atomic limit'
    assert summary['maximum_active'] <= 10
    assert summary['maximum_queue_interactive'] <= 32 and summary['maximum_queue_agent'] <= 32
    assert summary['final_outstanding'] == 0
    assert samples[-1]['active'] == 0 and samples[-1]['queue_agent'] == samples[-1]['queue_interactive'] == 0
    assert all(b['outstanding'] == 0 for b in samples[-1]['budgets'])
    assert summary['upstream_started'] == summary['upstream_completed'], 'Upstream did not drain'
    # Sampled over-limit outstanding after a reduction is explicitly permitted.
    summary['samples_above_reduced_limit'] = sum(any(b['outstanding'] > b['limit'] for b in e['budgets']) for e in samples)
    (path / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    with (path / 'timeseries.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['time_s', 'queue_interactive', 'queue_agent', 'active', 'principal', 'class', 'limit', 'outstanding', 'revision'])
        for event in samples:
            for budget in event['budgets']:
                writer.writerow([event['unix_s'] - config['start_unix_s'], event['queue_interactive'], event['queue_agent'],
                                 event['active'], budget['principal_id'], budget['class'], budget['limit'], budget['outstanding'], budget['revision']])
    return summary


def build_summary(root):
    runs = [analyze_run(path.parent) for path in sorted(root.glob('*/client.json')) if (path.parent / 'cleanup.json').exists()]
    (root / 'analysis-version.json').write_text(json.dumps({'analyzer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'incomplete_final_trace_samples': sum(r['incomplete_final_trace_samples'] for r in runs)}, indent=2) + '\n')
    (root / 'summary.json').write_text(json.dumps(runs, indent=2) + '\n')
    comparisons = []
    for scenario in dict.fromkeys(r['scenario'] for r in runs):
        group = [r for r in runs if r['scenario'] == scenario]
        for left, right in [('A', 'B'), ('B', 'C')]:
            for kind in ('interactive', 'agent'):
                def median(mode, key):
                    values = [r[kind][key] for r in group if r['mode'] == mode and r[kind][key] is not None]
                    return statistics.median(values) if values else None
                row = {'scenario': scenario, 'comparison': left + ' -> ' + right, 'class': kind}
                for key in ('successful_e2e_p95_ms', 'success_fraction', 'http_429', 'attempts_per_task', 'all_success_batch_ms'):
                    a, b = median(left, key), median(right, key)
                    row[key] = {'left_median': a, 'right_median': b, 'difference': b - a if a is not None and b is not None else None}
                comparisons.append(row)
    (root / 'comparisons.json').write_text(json.dumps(comparisons, indent=2) + '\n')
    def show(value):
        return 'n/a' if value is None else f'{value:.2f}'
    lines = ['# Automatically generated benchmark summary', '',
             'Latency percentiles use successful logical tasks only, measured from scheduled arrival.',
             'Every row is one run. No pooled percentile is inferred from run percentiles.', '',
             '| Scenario | Rep | Mode | Class | Success/total | Failed | Unfinished | p50 ms | p95 ms | p99 ms | Attempts/task | 429 | Queue timeout | Network/upstream errors | Agent batch ms* |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for run in runs:
        for kind in ('interactive', 'agent'):
            r = run[kind]
            lines.append(f"| {run['scenario']} | {run['repetition']} | {run['mode']} | {kind} | {r['success']}/{r['tasks']} | {r['failed']} | {r['unfinished']} | " +
                         ' | '.join(show(r[k]) for k in ('successful_e2e_p50_ms', 'successful_e2e_p95_ms', 'successful_e2e_p99_ms', 'attempts_per_task')) +
                         f" | {r['http_429']} | {r['queue_timeout']} | {r['network_errors']}/{r['upstream_errors']} | {show(r['all_success_batch_ms']) if kind == 'agent' else 'n/a'} |")
    lines += ['', '*Batch time is present only when every agent task succeeded; n/a is not a fast completion.', '',
              '## Separate A/B and B/C comparisons', '',
              'Values below are medians across runs, including explicitly the median of per-run p95 values.', '',
              '| Scenario | Comparison | Class | Median run p95: left -> right (ms) | Median success fraction: left -> right | Median 429: left -> right |',
              '|---|---|---|---|---|---|']
    for row in comparisons:
        def pair(key):
            return show(row[key]['left_median']) + ' -> ' + show(row[key]['right_median'])
        lines.append(f"| {row['scenario']} | {row['comparison']} | {row['class']} | {pair('successful_e2e_p95_ms')} | {pair('success_fraction')} | {pair('http_429')} |")
    lines += ['', 'See summary.json for exact fractions, actual upstream counts, generator lag, and admission checks.',
              'See each run directory for raw events, reconstructed tasks, and queue/executor/budget timeseries.',
              f"Incomplete final diagnostic samples: {sum(r['incomplete_final_trace_samples'] for r in runs)}. Raw files are preserved; interior corruption is fatal."]
    (root / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    return runs


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('results', type=Path)
    build_summary(parser.parse_args().results)
