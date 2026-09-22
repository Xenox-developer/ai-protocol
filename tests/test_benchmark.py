"""Measurement accounting checks independent of wall-clock timing."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import json

PATH = Path(__file__).resolve().parents[1] / 'benchmarks/stage4/analyze.py'
SPEC = importlib.util.spec_from_file_location('benchmark_analysis', PATH)
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def task(name):
    return {'id': name, 'class': 'agent', 'owner': 'demo-owner', 'arrival_ms': 100,
            'operation': 'search', 'params': {'query': name}}


class BenchmarkMeasurements(unittest.TestCase):
    def test_client_wait_and_retries_belong_to_one_logical_task(self):
        events = [
            {'event': 'arrival', 'id': 'one', 'at_ms': 105},
            {'event': 'attempt_start', 'id': 'one', 'number': 1, 'at_ms': 500},
            {'event': 'attempt_end', 'id': 'one', 'number': 1, 'at_ms': 510, 'status': 429},
            {'event': 'attempt_start', 'id': 'one', 'number': 2, 'at_ms': 1510},
            {'event': 'attempt_end', 'id': 'one', 'number': 2, 'at_ms': 1600, 'status': 200},
            {'event': 'terminal', 'id': 'one', 'at_ms': 1600, 'outcome': 'success', 'reason': 'completed'},
        ]
        records = analysis.reconstruct([task('one')], events)
        self.assertEqual(records[0]['e2e_ms'], 1500)
        result = analysis.summarize(records)['agent']
        self.assertEqual(result['tasks'], 1)
        self.assertEqual(result['attempts_per_task'], 2)
        self.assertEqual(result['http_429'], 1)
        self.assertEqual(result['successful_e2e_p95_ms'], 1500)

    def test_errors_missing_results_and_generator_rejections_remain_in_denominator(self):
        tasks = [task(name) for name in ('ok', 'rejected', 'unfinished', 'capacity')]
        events = [
            {'event': 'terminal', 'id': 'ok', 'at_ms': 200, 'outcome': 'success', 'reason': 'completed'},
            {'event': 'terminal', 'id': 'rejected', 'at_ms': 1000, 'outcome': 'failed', 'reason': 'Operation HTTP status 429'},
            {'event': 'terminal', 'id': 'capacity', 'at_ms': 100, 'outcome': 'failed', 'reason': 'generator_capacity'},
        ]
        result = analysis.summarize(analysis.reconstruct(tasks, events))['agent']
        self.assertEqual((result['success'], result['failed'], result['unfinished']), (1, 2, 1))
        self.assertEqual(result['success_fraction'], .25)
        self.assertEqual(result['generator_rejections'], 1)
        self.assertEqual(result['successful_e2e_p95_ms'], 100)
        self.assertIsNone(result['all_success_batch_ms'])

    def test_cancelled_attempt_is_recorded_without_fabricated_response(self):
        records = analysis.reconstruct([task('one')], [
            {'event': 'attempt_start', 'id': 'one', 'number': 1, 'at_ms': 110},
            {'event': 'terminal', 'id': 'one', 'at_ms': 150, 'outcome': 'unfinished', 'reason': 'run_deadline_or_cancelled'},
        ])
        self.assertEqual(records[0]['attempts'][0]['result'], 'incomplete')
        self.assertIsNone(records[0]['e2e_ms'])
        result = analysis.summarize(records)['agent']
        self.assertEqual(result['unfinished'], 1)
        self.assertIsNone(result['successful_e2e_p95_ms'])

    def test_empty_percentiles_are_absent_and_nearest_rank_is_explicit(self):
        self.assertIsNone(analysis.percentile([], .95))
        self.assertEqual(analysis.percentile([10, 30, 20], .50), 20)
        self.assertEqual(analysis.percentile([10, 30, 20], .95), 30)

    def test_only_incomplete_final_diagnostic_sample_is_tolerated_and_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.jsonl'
            path.write_text('{"active":0}\n{"active":0,"budgets":[')
            events, incomplete = analysis.read_trace(path)
            self.assertEqual(events, [{'active': 0}])
            self.assertEqual(incomplete, 1)
            path.write_text('{"active":0}\n{"active":0,"budgets":[\n{}\n')
            with self.assertRaises(json.JSONDecodeError):
                analysis.read_trace(path)
            path.write_text('{"event":"admission",')
            with self.assertRaises(json.JSONDecodeError):
                analysis.read_trace(path)
