"""Local agent connector. Exactly one submission; never replay an unknown outcome."""
import asyncio
import json
import math
import os
import re
import time
import uuid

MAX_FRAME = 64 * 1024


class DispatcherClient:
    def __init__(self, socket_path=None, token=None):
        self.socket_path = socket_path or os.environ['DISPATCH_SOCKET']
        self.token = token or os.environ['AGENT_TOKEN_1']

    async def call(self, operation, params, *, timeout_s=10, deadline_unix_ms=None):
        # Pass an existing absolute deadline if the task arrived before this call.
        deadline = deadline_unix_ms if deadline_unix_ms is not None else (time.time() + timeout_s) * 1000
        remaining = deadline / 1000 - time.time()
        def error(code, execution):
            return dict(ok=False, code=code, execution=execution, attempts=None if execution == 'unknown' else 0, status=None, body=None)
        if not math.isfinite(remaining):
            return error('invalid_dispatch_request', 'not_started')
        if remaining <= 0:
            return error('task_deadline', 'not_started')
        if isinstance(operation, dict):
            if __package__:
                from .protocol_client import validate_operation
            else:
                from protocol_client import validate_operation
            import jsonschema
            try:
                validate_operation(operation)
                jsonschema.Draft202012Validator(operation['input_schema']).validate(params)
            except (ValueError, jsonschema.ValidationError, jsonschema.SchemaError):
                return error('invalid_dispatch_request', 'not_started')
            operation = operation['name']
        if remaining > 3600 or not isinstance(operation, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', operation):
            return error('invalid_dispatch_request', 'not_started')
        try:
            payload = json.dumps(dict(id=uuid.uuid4().hex, token=self.token, operation=operation,
                                      params=params, deadline_unix_ms=deadline), allow_nan=False).encode() + b'\n'
        except (TypeError, ValueError):
            return error('invalid_dispatch_request', 'not_started')
        if len(payload) > MAX_FRAME:
            return error('invalid_dispatch_request', 'not_started')
        writer = None
        connected = False
        async def exchange():
            nonlocal writer, connected
            reader, writer = await asyncio.open_unix_connection(self.socket_path, limit=MAX_FRAME)
            connected = True
            writer.write(payload)
            await writer.drain()
            raw = await reader.readline()
            if len(raw) > MAX_FRAME or not raw.endswith(b'\n'):
                raise ValueError('Incomplete dispatcher reply')
            reply = json.loads(raw)
            if not isinstance(reply, dict) or not isinstance(reply.get('ok'), bool) or not isinstance(reply.get('code'), str) or reply.get('execution') not in ('completed', 'not_started', 'unknown'):
                raise ValueError('Invalid dispatcher reply')
            return reply
        remaining = deadline / 1000 - time.time()
        if remaining <= 0:
            return error('task_deadline', 'not_started')
        try:
            return await asyncio.wait_for(exchange(), remaining)
        except (OSError, ValueError, asyncio.TimeoutError):
            return error('dispatcher_response_lost' if connected else 'dispatcher_unavailable',
                         'unknown' if connected else 'not_started')
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', help='Discovered operation name (legacy route aliases are also supported)')
    parser.add_argument('params', help='JSON operation parameters; credentials come only from the environment')
    args = parser.parse_args()
    result = asyncio.run(DispatcherClient().call(args.operation, json.loads(args.params)))
    print(json.dumps(result))
    raise SystemExit(0 if result['ok'] else 1)
