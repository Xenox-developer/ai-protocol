# Python SDK

Python 3.12 or newer is required. Install from the checkout with
`python -m pip install .`, or install a built wheel. The distribution name is
`ai-protocol-client`; the import name is `ai_protocol`. No PyPI publication is
claimed. The ordinary SDK depends on httpx and jsonschema. OpenAI is optional:
`python -m pip install '.[llm]'` for the separate LLM example.

## Adaptive calls

```python
import asyncio
import os
from ai_protocol import AsyncClient, OperationError

async def main():
    async with AsyncClient("http://127.0.0.1:3000", os.environ["AGENT_TOKEN_1"]) as client:
        print(client.policy["operations"])
        try:
            result = await client.call("get_product", {"id": 2}, timeout=30)
            print(result)
        except OperationError as error:
            print(error.code, error.execution, error.attempts)

asyncio.run(main())
```

One client owns one policy refresher and one local gate shared by its concurrent
calls. It starts with authenticated discovery, applies advertised revisions,
pauses new attempts on refresh failure and recovers only on a valid policy.
An owner/class/service change or revision regression pauses work. Shrinking the
ceiling preserves active attempts; growth wakes waiters. `policy` returns a
detached full snapshot; `ready` and `active_attempts` expose local state.

The gate is not a reservation of server capacity and cannot coordinate other
processes. The server remains authoritative and can return 429. Discovery does
not consume an operation slot. Parameter schemas are validated before sending.
External schema references and operation paths outside the configured origin
are rejected; redirects and environment proxies are disabled.

`call(name, params, timeout=120, deadline_unix_ms=None)` applies one deadline to
gate waiting, HTTP and retry waits. An absolute Unix deadline overrides the
relative timeout; it cannot be more than one hour ahead. Five attempts at most
share that deadline. Only 429 and 503 `queue_timeout` with
`execution=not_started` authorize automatic retries. Retry-After is respected.
Other HTTP failures, transport loss and unknown outcomes are terminal.

## Failure and lifecycle contract

- `ProtocolError`: invalid configuration/discovery. Messages omit credentials.
- `OperationError`: `code`, `execution`, `status`, `attempts` describe a failed call.
- `DeadlineExceeded`: an OperationError for an exhausted logical deadline.
- `execution=not_started`: no attempt was sent, or an authoritative rejection
  proves execution did not begin. This alone does not authorize an automatic retry.
- `execution=unknown`: an attempt may have executed; do not blindly replay it.

Cancelling a call propagates `asyncio.CancelledError`; an already sent operation
may still execute remotely. Local permits are released. Await all calls before
leaving `async with`; `aclose()` stops new attempts, cancels the owned poller and
closes HTTP transport. Closing with in-flight work may interrupt its reply and
leave an unknown remote outcome. A closed client cannot be reopened.

Responses are buffered up to `max_response_bytes` (default 16 MiB); discovery is
bounded to 1 MiB. Oversized operation responses are terminal with unknown execution.
The SDK emits no request bodies or credentials to logs.

## Optional dispatcher

Pass `dispatcher_socket="/private/path/socket"` to AsyncClient. Discovery still
uses that agent's credential and configured origin. Work is submitted once to
`DispatcherClient`, preserving the original deadline and credential; there is no
extra direct-HTTP attempt gate, fallback or automatic resubmission on reply loss.
The dispatcher must belong to the same service and owner. See [dispatcher setup](dispatcher.md).

`DispatcherClient(socket_path, token).call(descriptor, params,
deadline_unix_ms=...)` remains available directly and returns its existing reply
envelope. `discover`, `execute` and `service_client` remain compatible synchronous
helpers. They retain bounded retries but do not add adaptive policy polling;
use AsyncClient for cooperative adaptation. Old example imports are compatibility
wrappers; new applications import from the installed `ai_protocol` package.
