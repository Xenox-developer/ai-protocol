"""Independent workers partition arrivals, retain ownership, and refresh separately."""
import importlib.util
from pathlib import Path
import sys
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'benchmarks/multiclient'))
from analyze_clients import client_policy, validate_partition
SPEC = importlib.util.spec_from_file_location('multiclient_runner', BASE / 'benchmarks/multiclient/run.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class IndependentClients(unittest.TestCase):
    def test_partition_preserves_aggregate_arrivals_and_operations(self):
        import json
        for scenario in ('mixed_overload', 'dynamic'):
            source = json.loads((BASE / f'benchmarks/results/stage4-main/{scenario}-schedule.json').read_text())
            tasks = [t for t in source if t['owner'] == 'demo-owner']
            for count in (1, 2, 4):
                groups = runner.partition(tasks, count)
                assigned = sorted((t for g in groups for t in g['tasks']), key=lambda t: t['arrival_ms'])
                self.assertEqual(assigned, tasks)
                self.assertEqual(len({g['token_env'] for g in groups}), count + 1)
                self.assertEqual([len(g['tasks']) for g in groups[:-1]],
                                 [sum(t['class'] == 'agent' for t in tasks) // count] * count)
                for group in groups:
                    self.assertEqual(group['tasks'], sorted(group['tasks'], key=lambda t: t['arrival_ms']))

    def test_duplicate_or_migrating_task_is_rejected(self):
        tasks = [{'id': 'one'}, {'id': 'two'}]
        manifest = [{'id': 'a', 'pid': 10, 'token_env': 'TOKEN_1', 'tasks': ['one']},
                    {'id': 'b', 'pid': 11, 'token_env': 'TOKEN_2', 'tasks': ['two']}]
        events = [{'event': event, 'id': task, 'client_id': client}
                  for task, client in [('one', 'a'), ('two', 'b')] for event in ('arrival', 'terminal')]
        self.assertEqual(validate_partition(tasks, manifest, events), {'one': 'a', 'two': 'b'})
        with self.assertRaises(AssertionError):
            validate_partition(tasks, manifest, events + [events[0]])
        events[-1]['client_id'] = 'a'
        with self.assertRaises(AssertionError):
            validate_partition(tasks, manifest, events)

    def test_each_client_must_apply_both_changes_independently(self):
        changes = [{'request_started_s': t, 'actual_s': t + .01,
                    'response': {'principal_id': 'demo-owner', 'policy_revision': revision,
                                 'limits': {'max_outstanding': limit}}}
                   for t, revision, limit in [(2, 2, 2), (5, 3, 5)]]
        initial = [{'event': 'discovery_start', 'owner': 'demo-owner'},
                   {'event': 'discovery_end', 'success': True},
                   {'event': 'policy', 'owner': 'demo-owner', 'ready': True, 'revision': 1, 'limit': 5, 'at_ms': -400}]
        fixed = initial + [{'event': 'gate_final', 'owner': 'demo-owner', 'active': 0, 'revision': 1, 'limit': 5}]
        self.assertEqual(client_policy(fixed, changes, 'D')['requests'], 1)
        with self.assertRaises(AssertionError):
            client_policy(fixed, changes, 'C')
        adaptive = initial + [dict(event='policy', owner='demo-owner', ready=True, revision=r, limit=n, at_ms=t)
                              for r, n, t in [(2, 2, 2600), (3, 5, 5600)]]
        adaptive += [{'event': 'gate_final', 'owner': 'demo-owner', 'active': 0, 'revision': 3, 'limit': 5}]
        for delay in client_policy(adaptive, changes, 'C')['application_delays']:
            self.assertAlmostEqual(delay['delay_lower_ms'], 590)
