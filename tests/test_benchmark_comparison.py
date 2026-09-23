"""C/D analysis uses full arrival-cohort outcomes and explicit change-time bounds."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'benchmarks/stage4_1/comparison.py'
SPEC = importlib.util.spec_from_file_location('policy_comparison', PATH)
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


class PolicyComparison(unittest.TestCase):
    def test_application_delay_uses_revision_and_admin_interval(self):
        change = {'request_started_s':2.0, 'actual_s':2.01,
                  'response':{'principal_id':'demo-owner', 'policy_revision':2,
                              'limits':{'max_outstanding':2}}}
        events = [{'event':'policy','owner':'demo-owner','ready':True,'limit':5,'revision':1,'at_ms':-400},
                  {'event':'policy','owner':'demo-owner','ready':True,'limit':2,'revision':2,'at_ms':2510},
                  {'event':'policy','owner':'demo-owner','ready':True,'limit':2,'revision':2,'at_ms':3510}]
        delay = comparison.application_delays(events, [change])[0]
        self.assertAlmostEqual(delay['delay_lower_ms'], 500)
        self.assertAlmostEqual(delay['delay_upper_ms'], 510)
        self.assertIsNone(comparison.application_delays(events[:1], [change])[0]['applied_ms'])

    def test_reduced_arrival_retains_late_result_and_full_latency(self):
        tasks = [{'id':'one','class':'agent','owner':'demo-owner','arrival_ms':2100,
                  'operation':'search','params':{'query':'one'}}]
        events = [{'event':'discovery_start','owner':'demo-owner','at_ms':-400},
                  {'event':'arrival','id':'one','at_ms':2101},
                  {'event':'attempt_start','id':'one','number':1,'at_ms':2150},
                  {'event':'attempt_end','id':'one','number':1,'at_ms':2160,'status':429},
                  {'event':'attempt_start','id':'one','number':2,'at_ms':5100},
                  {'event':'attempt_end','id':'one','number':2,'at_ms':5300,'status':200},
                  {'event':'terminal','id':'one','at_ms':5300,'outcome':'success','reason':'completed'}]
        records = comparison.original.reconstruct(tasks, events)
        phases = comparison.phase_results(records, events, [], 0, True)
        reduced = phases['reduced']
        self.assertEqual(reduced['arrival_cohort']['agent']['tasks'], 1)
        self.assertEqual(reduced['arrival_cohort']['agent']['successful_e2e_p95_ms'], 3200)
        self.assertEqual(reduced['arrival_cohort']['agent']['attempts'], 2)
        self.assertEqual(reduced['activity']['http_429_responses'], 1)
        self.assertEqual(phases['restored']['arrival_cohort']['agent']['tasks'], 0)
        self.assertEqual(phases['restored']['activity']['terminal_outcomes'], {'success':1})
        self.assertEqual(phases['before_start']['activity']['discovery_starts'], 1)

    def test_final_error_reasons_do_not_drop_deadlines_or_unfinished_tasks(self):
        rows = [{'class':'agent','outcome':'failed','reason':'task_deadline'},
                {'class':'agent','outcome':'failed','reason':'Operation HTTP status 429'},
                {'class':'agent','outcome':'unfinished','reason':'run_deadline_or_cancelled'}]
        self.assertEqual(comparison.failure_reasons(rows)['agent'],
                         {'task_deadline':1, 'Operation HTTP status 429':1})
