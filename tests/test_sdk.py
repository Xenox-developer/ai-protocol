"""Public installed SDK behavior: adaptation, lifecycle, deadlines, no replay."""
import asyncio
import copy
import unittest

import httpx

from ai_protocol import AsyncClient, DeadlineExceeded, OperationError, ProtocolError


def policy(maximum=2, revision=1):
    return dict(version=3, service_id="records", principal_id="reader", client_class="agent",
                policy_revision=revision, refresh_after_ms=100, outstanding=0,
                limits=dict(scope="principal", max_outstanding=maximum), operations=[dict(
                    name="lookup", description="Read a record", method="POST", path="/records",
                    input_schema={"type": "object", "properties": {"id": {"type": "integer"}},
                                  "required": ["id"], "additionalProperties": False})])


class SDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_shrink_preserves_attempts_and_growth_wakes_waiters(self):
        current = policy()
        started = asyncio.Queue()
        release = [asyncio.Event() for _ in range(3)]

        async def handle(request):
            self.assertEqual(request.headers["authorization"], "Bearer test-sdk")
            if request.url.path == "/agent-policy":
                return httpx.Response(200, json=copy.deepcopy(current))
            import json
            index = json.loads(request.content)["id"]
            started.put_nowait(index)
            await release[index].wait()
            return httpx.Response(200, json={"id": index})

        async with AsyncClient("https://records.test", "test-sdk", transport=httpx.MockTransport(handle)) as client:
            detached = client.policy
            detached["limits"]["max_outstanding"] = 999
            self.assertEqual(client.policy["limits"]["max_outstanding"], 2)
            first = asyncio.create_task(client.call("lookup", {"id": 0}))
            second = asyncio.create_task(client.call("lookup", {"id": 1}))
            self.assertEqual({await started.get(), await started.get()}, {0, 1})
            current.update(policy(1, 2))
            await client.refresh()
            third = asyncio.create_task(client.call("lookup", {"id": 2}))
            await asyncio.sleep(0)
            self.assertTrue(started.empty())
            release[0].set()
            self.assertEqual(await first, {"id": 0})
            self.assertEqual(client.active_attempts, 1)
            self.assertTrue(started.empty())
            current.update(policy(2, 3))
            await client.refresh()
            self.assertEqual(await asyncio.wait_for(started.get(), 1), 2)
            release[1].set()
            release[2].set()
            await asyncio.gather(second, third)
            self.assertEqual(client.active_attempts, 0)
        self.assertFalse(client.ready)

    async def test_refresh_failure_pauses_new_work_until_valid_recovery(self):
        failing = False
        calls = []

        def handle(request):
            if request.url.path == "/agent-policy":
                return httpx.Response(503) if failing else httpx.Response(200, json=policy())
            calls.append(request)
            return httpx.Response(200, json={"ok": True})

        async with AsyncClient("https://records.test", "test-sdk", transport=httpx.MockTransport(handle)) as client:
            failing = True
            with self.assertRaises(ProtocolError):
                await client.refresh()
            self.assertFalse(client.ready)
            waiting = asyncio.create_task(client.call("lookup", {"id": 1}))
            await asyncio.sleep(0)
            self.assertEqual(calls, [])
            failing = False
            await client.refresh()
            self.assertEqual(await waiting, {"ok": True})

    async def test_retry_reasons_share_attempt_budget_and_unknown_loss_never_replays(self):
        attempts = []
        loss = False

        def handle(request):
            if request.url.path == "/agent-policy":
                return httpx.Response(200, json=policy())
            attempts.append(request)
            if loss:
                raise httpx.ReadError("test-sensitive-token")
            if len(attempts) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": {"code": "queue_timeout", "execution": "not_started"}})

        async with AsyncClient("https://records.test", "test-sdk", transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(OperationError) as error:
                await client.call("lookup", {"id": 1})
            self.assertEqual(len(attempts), 5)
            self.assertEqual(error.exception.execution, "not_started")
            loss = True
            with self.assertRaises(OperationError) as error:
                await client.call("lookup", {"id": 1})
            self.assertEqual(len(attempts), 6)
            self.assertEqual(error.exception.execution, "unknown")
            self.assertNotIn("test-sensitive-token", str(error.exception))
            self.assertEqual(client.active_attempts, 0)

    async def test_deadline_during_execution_is_unknown_without_replay(self):
        attempts = 0

        async def handle(request):
            nonlocal attempts
            if request.url.path == "/agent-policy":
                return httpx.Response(200, json=policy())
            attempts += 1
            await asyncio.Event().wait()

        async with AsyncClient("https://records.test", "test-sdk", transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(DeadlineExceeded) as error:
                await client.call("lookup", {"id": 1}, timeout=0.05)
            self.assertEqual(error.exception.execution, "unknown")
            self.assertEqual(attempts, 1)
            self.assertEqual(client.active_attempts, 0)

    async def test_invalid_parameters_and_closed_gateway_never_retry(self):
        attempts = 0

        def handle(request):
            nonlocal attempts
            if request.url.path == "/agent-policy":
                return httpx.Response(200, json=policy())
            attempts += 1
            return httpx.Response(503, json={"error": {"code": "gateway_closed", "execution": "not_started"}})

        async with AsyncClient("https://records.test", "test-sdk", transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(OperationError):
                await client.call("lookup", {"id": "invalid"})
            self.assertEqual(attempts, 0)
            with self.assertRaises(OperationError) as error:
                await client.call("lookup", {"id": 1})
            self.assertEqual(attempts, 1)
            self.assertEqual(error.exception.execution, "not_started")

    async def test_changed_identity_pauses_and_deadline_in_queue_is_not_started(self):
        current = policy()
        def handle(request):
            return httpx.Response(200, json=current)
        async with AsyncClient("https://records.test", "test-sdk", transport=httpx.MockTransport(handle)) as client:
            current["service_id"] = "different"
            with self.assertRaises(ProtocolError):
                await client.refresh()
            with self.assertRaises(DeadlineExceeded) as error:
                await client.call("lookup", {"id": 1}, timeout=0.05)
            self.assertEqual(error.exception.execution, "not_started")


if __name__ == "__main__":
    unittest.main()

    async def test_background_refresher_runs_and_stops_with_context(self):
        polls = asyncio.Queue()
        def handle(request):
            polls.put_nowait(True)
            return httpx.Response(200, json=policy())
        async with AsyncClient("https://records.test", "test-sdk", transport=httpx.MockTransport(handle)) as client:
            await polls.get()
            await asyncio.wait_for(polls.get(), 1)
            self.assertTrue(client.ready)
        self.assertIsNone(client._refresher)

    async def test_dispatcher_unknown_outcome_is_typed_and_submitted_once(self):
        import time
        from unittest.mock import AsyncMock
        def handle(request):
            self.assertEqual(request.url.path, "/agent-policy")
            return httpx.Response(200, json=policy())
        async with AsyncClient("https://records.test", "test-sdk", dispatcher_socket="/unused",
                               transport=httpx.MockTransport(handle)) as client:
            client._dispatcher.call = AsyncMock(return_value=dict(ok=False, code="dispatcher_response_lost", execution="unknown", attempts=None))
            deadline = int((time.time() + 10) * 1000)
            with self.assertRaises(OperationError) as result:
                await client.call("lookup", {"id": 1}, deadline_unix_ms=deadline)
            self.assertEqual(result.exception.execution, "unknown")
            client._dispatcher.call.assert_awaited_once()
            self.assertEqual(client._dispatcher.call.call_args.kwargs["deadline_unix_ms"], deadline)
