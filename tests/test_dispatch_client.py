"""The local connector preserves credentials/deadlines and never replays lost replies."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'examples'))
from dispatch_client import DispatcherClient


class DispatchConnector(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dispatch-test-', dir='/tmp')
        self.path = str(Path(self.directory.name) / 'socket')
        self.calls = []
        self.payload = None
        async def respond(reader, writer):
            self.calls.append(json.loads(await reader.readline()))
            if self.payload is not None:
                writer.write(self.payload)
                await writer.drain()
            writer.close()
            await writer.wait_closed()
        self.server = await asyncio.start_unix_server(respond, self.path)
        self.client = DispatcherClient(self.path, 'restricted-test-token')

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.directory.cleanup()

    async def test_original_deadline_and_credential_are_forwarded_without_new_budget(self):
        expected = dict(ok=True, code='completed', execution='completed', attempts=1, status=200, body={'product':{'id':4}})
        self.payload = json.dumps(expected).encode() + b'\n'
        deadline = time.time()*1000 + 500
        result = await self.client.call('product', {'id':4}, deadline_unix_ms=deadline)
        self.assertEqual(result, expected)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['token'], 'restricted-test-token')
        self.assertEqual(self.calls[0]['deadline_unix_ms'], deadline)

    async def test_response_loss_is_unknown_and_never_replayed(self):
        result = await self.client.call('search', {'query':'x'})
        self.assertEqual((result['code'], result['execution']), ('dispatcher_response_lost','unknown'))
        self.assertIsNone(result['attempts'])
        self.assertEqual(len(self.calls), 1)

    async def test_queue_full_is_returned_without_automatic_resubmission(self):
        self.payload = json.dumps(dict(ok=False, code='dispatcher_queue_full', execution='not_started', attempts=0)).encode() + b'\n'
        result = await self.client.call('search', {'query':'x'})
        self.assertEqual(result['code'], 'dispatcher_queue_full')
        self.assertEqual(len(self.calls), 1)

    async def test_unavailable_expired_and_malformed_reply_are_explicit(self):
        missing = DispatcherClient(self.path + '-missing', 'restricted-test-token')
        self.assertEqual((await missing.call('search', {}))['code'], 'dispatcher_unavailable')
        result = await self.client.call('search', {}, deadline_unix_ms=time.time()*1000-1)
        self.assertEqual(result['execution'], 'not_started')
        self.assertEqual(len(self.calls), 0)
        self.payload = b'[]\n'
        result = await self.client.call('search', {})
        self.assertEqual(result['execution'], 'unknown')
        self.assertEqual(len(self.calls), 1)

    async def test_discovered_operation_uses_its_name_without_catalog_specific_code(self):
        operation = dict(name='lookup_case', description='Read a case', method='POST', path='/cases/lookup',
                         input_schema={'type':'object', 'properties':{'id':{'type':'integer'}}, 'required':['id'], 'additionalProperties':False})
        self.payload = json.dumps(dict(ok=True, code='completed', execution='completed', attempts=1, status=200, body={'case':{'id':7}})).encode() + b'\n'
        result = await self.client.call(operation, {'id':7})
        self.assertTrue(result['ok'])
        self.assertEqual(self.calls[0]['operation'], 'lookup_case')
        self.assertEqual(self.calls[0]['token'], 'restricted-test-token')
        invalid = await self.client.call(operation, {'id':'bad'})
        self.assertEqual(invalid['code'], 'invalid_dispatch_request')
        self.assertEqual(len(self.calls), 1)
