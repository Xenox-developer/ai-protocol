"""Live diagnostics are visible as records only after the newline is written."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from service_integration_demo import rows


class LiveTraceTests(unittest.TestCase):
    def test_partial_json_and_utf8_tail_waits_for_record_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_bytes(b'{"event":"first"}\n{"event":')
            self.assertEqual(rows(path), [{"event": "first"}])
            path.write_bytes(b'{"event":"first"}\n{"event":"next","value":"\xe2')
            self.assertEqual(rows(path), [{"event": "first"}])
            with path.open("ab") as output:
                output.write(b'\x98\x83"}\n')
            self.assertEqual(rows(path), [{"event": "first"}, {"event": "next", "value": "\u2603"}])

    def test_corrupted_complete_record_is_never_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_bytes(b'{"event":broken}\n')
            with self.assertRaises(json.JSONDecodeError):
                rows(path)
