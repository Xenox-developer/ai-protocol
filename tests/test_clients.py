import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx
import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
import llm_client
import protocol_client as protocol
import setup_demo


OPERATION = {
    "name": "search_products",
    "description": "Search by substring; an empty query returns all products.",
    "method": "POST",
    "path": "/search",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
}
POLICY = {
    "version": 3,
    "policy_revision": 1,
    "refresh_after_ms": 1000,
    "outstanding": 0,
    "queue": {"max_wait_ms": 10000},
    "principal_id": "demo-owner",
    "client_class": "agent",
    "limits": {"scope": "principal", "max_outstanding": 5},
    "operations": [OPERATION],
}


class ClientTests(unittest.TestCase):
    def test_service_client_requires_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "AGENT_TOKEN_1"):
                protocol.service_client()
        for invalid in ("test-secret\n", "test-secret with spaces", "test-secret-\u00e9"):
            with patch.dict(os.environ, {"AGENT_TOKEN_1": invalid}):
                with self.assertRaises(ValueError) as error:
                    protocol.service_client()
                self.assertNotIn(invalid, str(error.exception))
        with patch.dict(os.environ, {"AGENT_TOKEN_1": "test-service-token"}):
            with protocol.service_client() as client:
                self.assertEqual(client.headers["Authorization"], "Bearer test-service-token")
                self.assertFalse(client.follow_redirects)

    def test_discovery_rejects_incompatible_or_invalid_policy(self):
        invalid = []
        for version in (1, 2, True, "3"):
            invalid.append(dict(POLICY, version=version))
        for maximum in (0, -1, True, "5"):
            invalid.append(dict(POLICY, limits={"scope": "principal", "max_outstanding": maximum}))
        invalid.extend([
            dict(POLICY, principal_id=""), dict(POLICY, client_class="human"),
            dict(POLICY, limits={"scope": "connection", "max_outstanding": 5}),
            dict(POLICY, operations=[OPERATION, OPERATION]),
        ])
        for policy in invalid:
            with self.subTest(policy=policy), httpx.Client(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=policy)
            )) as client:
                with self.assertRaises(ValueError):
                    protocol.discover(client)
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=POLICY))) as client:
            self.assertEqual(protocol.discover(client), [OPERATION])

    def test_retry_after_and_attempt_budget(self):
        for header, delay in [("2", 2), ("0", 0), ("bad", 1), ("-1", 1), ("", 1)]:
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(429, headers={"Retry-After": header})
            with self.subTest(header=header), httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with patch.object(protocol.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(httpx.HTTPStatusError):
                        protocol.execute(client, OPERATION, {"query": ""})
                    self.assertEqual(len(requests), 5)
                    self.assertEqual([call.args for call in sleep.call_args_list], [(delay,)] * 4)

    def test_retry_then_success_keeps_authorization(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer test-service-token")
            if len(requests) == 1:
                return httpx.Response(429, headers={"Retry-After": "2"})
            return httpx.Response(200, json={"products": []})
        with httpx.Client(headers={"Authorization": "Bearer test-service-token"}, transport=httpx.MockTransport(handler)) as client:
            with patch.object(protocol.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(protocol.execute(client, OPERATION, {"query": ""}), {"products": []})
                sleep.assert_called_once_with(2)
                self.assertNotIn("test-service-token", output.getvalue())
        self.assertEqual(len(requests), 2)

    def test_queue_timeout_retry_shares_attempt_budget_with_429(self):
        timeout_body = {"error": {"code": "queue_timeout", "execution": "not_started"}}
        now = [0.0]
        calls = []
        def handler(request):
            calls.append(now[0])
            status = 429 if len(calls) % 2 else 503
            return httpx.Response(status, headers={"Retry-After": "2"}, json=timeout_body)
        def sleep(seconds):
            now[0] += seconds
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch.object(protocol.time, "monotonic", side_effect=lambda: now[0]), patch.object(protocol.time, "sleep", side_effect=sleep):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(httpx.HTTPStatusError):
                        protocol.execute(client, OPERATION, {"query": ""})
        self.assertEqual(calls, [0, 2, 4, 6, 8])

    def test_only_documented_queue_timeout_is_retryable(self):
        for status, body in [
            (503, {"error": {"code": "queue_timeout", "execution": "started"}}),
            (503, {"error": {"code": "upstream_timeout", "execution": "not_started"}}),
            (503, {"error": {"code": "queue_timeout"}}),
            (503, {"error": []}), (503, []), (503, None),
            (502, {"error": {"code": "queue_timeout", "execution": "not_started"}}),
        ]:
            handler = MagicMock(return_value=httpx.Response(status, json=body))
            with self.subTest(status=status, body=body), httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(httpx.HTTPStatusError):
                    protocol.execute(client, OPERATION, {"query": ""})
                self.assertEqual(handler.call_count, 1)
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(503, headers={"Retry-After": "1"}, json={
                    "error": {"code": "queue_timeout", "execution": "not_started"}})
            return httpx.Response(200, json={"products": []})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch.object(protocol.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(protocol.execute(client, OPERATION, {"query": ""}), {"products": []})
                sleep.assert_called_once_with(1)
        self.assertEqual(len(calls), 2)

    def test_operation_deadline_prevents_late_retry(self):
        handler = MagicMock(return_value=httpx.Response(503, headers={"Retry-After": "10"}, json={
            "error": {"code": "queue_timeout", "execution": "not_started"}}))
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch.object(protocol.time, "monotonic", return_value=0), patch.object(protocol.time, "sleep") as sleep:
                with self.assertRaises(TimeoutError):
                    protocol.execute(client, OPERATION, {"query": ""}, task_timeout=3)
                sleep.assert_not_called()
                self.assertEqual(handler.call_count, 1)

    def test_other_errors_are_not_retried(self):
        for status in (401, 403, 500, 502, 504):
            handler = MagicMock(return_value=httpx.Response(status))
            with self.subTest(status=status), httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(httpx.HTTPStatusError):
                    protocol.execute(client, OPERATION, {"query": ""})
                self.assertEqual(handler.call_count, 1)
        handler = MagicMock(side_effect=httpx.ReadTimeout("Test timeout"))
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(httpx.ReadTimeout):
                protocol.execute(client, OPERATION, {"query": ""})
            self.assertEqual(handler.call_count, 1)

    def test_external_paths_and_schemas_are_rejected_before_request(self):
        for path in ("https://example.com/search", "//example.com/search", "/../search", "/search?token=x", "/%2f%2fexample.com"):
            handler = MagicMock()
            with self.subTest(path=path), httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(ValueError):
                    protocol.execute(client, dict(OPERATION, path=path), {"query": ""})
                handler.assert_not_called()
        operation = copy.deepcopy(OPERATION)
        operation["input_schema"]["$ref"] = "https://example.com/schema"
        with self.assertRaises(ValueError):
            protocol.validate_operation(operation)

    def test_redirect_is_not_followed_and_parameters_are_validated(self):
        handler = MagicMock(return_value=httpx.Response(307, headers={"Location": "https://example.com/search"}))
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            with self.assertRaises(jsonschema.ValidationError):
                protocol.execute(client, OPERATION, {"query": 123})
            handler.assert_not_called()
            with self.assertRaises(httpx.HTTPStatusError):
                protocol.execute(client, OPERATION, {"query": ""})
            self.assertEqual(handler.call_count, 1)

    def test_llm_receives_tools_without_service_token(self):
        token = "test-private-service-token"
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers["authorization"], f"Bearer {token}")
            if request.url.path == "/agent-policy":
                return httpx.Response(200, json=POLICY)
            return httpx.Response(200, json={"products": []})
        service = httpx.Client(headers={"Authorization": f"Bearer {token}"}, transport=httpx.MockTransport(handler))
        model = MagicMock()
        model.__enter__.return_value = model
        model.responses.create.return_value = SimpleNamespace(output=[
            SimpleNamespace(type="function_call", name="search_products", arguments='{"query":""}')
        ])
        with patch.object(llm_client, "service_client", return_value=service), patch.object(llm_client, "OpenAI", return_value=model) as factory:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-model-key", "AGENT_TOKEN_1": token}), patch("builtins.input", return_value="List all products"):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    llm_client.main()
        self.assertEqual(len(requests), 2)
        self.assertNotIn(token, json.dumps(model.responses.create.call_args.kwargs))
        self.assertNotIn(token, json.dumps(factory.call_args.kwargs))
        self.assertNotIn(token, output.getvalue())

    def test_demo_credentials_are_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(setup_demo, "__file__", str(root / "examples" / "setup_demo.py")):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    setup_demo.main()
                destination = root / ".env.demo"
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
                original = destination.read_text()
                values = [line.split("=", 1)[1] for line in original.splitlines()]
                self.assertEqual(len(set(values)), len(setup_demo.TOKENS))
                for token in values:
                    self.assertNotIn(token, output.getvalue())
                with self.assertRaises(SystemExit):
                    setup_demo.main()
                self.assertEqual(destination.read_text(), original)


if __name__ == "__main__":
    unittest.main()
