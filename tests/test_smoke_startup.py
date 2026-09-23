"""Startup bounds and diagnostics, with controlled time rather than real sleeps."""
import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke


class StartupChecks(unittest.TestCase):
    def test_slow_start_gets_multiple_short_probes(self):
        clock = [0.0]
        calls = []
        def probe(url, timeout):
            calls.append(timeout)
            clock[0] += timeout
            if clock[0] < 12:
                raise httpx.ConnectError('starting')
            return SimpleNamespace(status_code=200)
        with patch.object(smoke.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(smoke.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            smoke.ready(SimpleNamespace(poll=lambda: None), SimpleNamespace(get=probe), 'http://127.0.0.1:1', 200)
        self.assertGreater(len(calls), 10)
        self.assertTrue(all(timeout <= 1 for timeout in calls))

    def test_bad_status_has_a_bounded_deadline_and_useful_error(self):
        clock = [0.0]
        with patch.object(smoke.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(smoke.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            with self.assertRaisesRegex(RuntimeError, 'within 0.1s.*HTTP 503, expected 200'):
                smoke.ready(SimpleNamespace(poll=lambda: None),
                            SimpleNamespace(get=lambda *args, **kwargs: SimpleNamespace(status_code=503)),
                            'http://127.0.0.1:1', 200, startup_timeout=0.1)
        self.assertAlmostEqual(clock[0], 0.1)

    def test_early_exit_and_child_output_are_reported_without_tokens(self):
        with self.assertRaisesRegex(RuntimeError, 'exit=7'):
            smoke.ready(SimpleNamespace(poll=lambda: 7), None, 'http://127.0.0.1:1', 200)
        output = io.StringIO()
        secret = 'temporary-test-secret'
        with contextlib.redirect_stderr(output):
            with self.assertRaisesRegex(RuntimeError, 'intentional'):
                with smoke.running([sys.executable, '-c', 'import os; print(os.environ["AGENT_TOKEN_1"], flush=True)'],
                                   {'AGENT_TOKEN_1': secret}) as process:
                    self.assertEqual(process.wait(timeout=5), 0)
                    raise RuntimeError('intentional')
        self.assertIn('<redacted>', output.getvalue())
        self.assertNotIn(secret, output.getvalue())
