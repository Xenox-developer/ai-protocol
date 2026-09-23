# Local owner dispatcher

The optional `dispatcher` binary coordinates agent processes on one machine.
One instance handles one gateway and **one agent owner**, verified through v3
policy. All registered credentials must resolve to that same owner and class.
Other service origins and owners use separate instances and private sockets.
Existing direct clients and service policy v3 remain compatible. See
[service integration](service-integration.md) for the generic operation mapping.

## Budget meaning

The term `max_in_flight` describes the **aggregate owner budget**, not a personal
quota for each agent process. The current v3 wire field is
`limits.max_outstanding`, with existing `limits.scope = "principal"` and
`client_class = "agent"`. The optional additive `service_id` field identifies
the configured service without renaming existing fields. The dispatcher binds
each Gate to its fixed gateway origin plus verified principal/class and checks
service ID consistency. Identical principal IDs on different services never
share a Gate. The gateway scope is one service process + principal + class, covering
accepted queued and running work. Discovery describes a ceiling; neither the
limit nor the advisory outstanding snapshot reserves places.

The dispatcher applies that ceiling to one shared Gate for active HTTP attempts
from its connected clients. Other clients of the same owner can still consume
server capacity. Policy refresh is delayed, so 429 remains possible and valid.
A local active-attempt count is not an authoritative server outstanding count.

## Start and connect

Start the catalog and gateway with the existing README instructions. In another
terminal, from the repository root:

```sh
source .env.demo
cargo build --locked --release --bin dispatcher
export DISPATCH_SOCKET_DIR="$(mktemp -d /tmp/ai-dispatch.XXXXXX)"
chmod 700 "$DISPATCH_SOCKET_DIR"
export DISPATCH_SOCKET="$DISPATCH_SOCKET_DIR/socket"
export DISPATCH_TOKEN_VARS=AGENT_TOKEN_1,AGENT_TOKEN_2,PRODUCT_ONLY_TOKEN
export DISPATCH_CAPACITY=1024
./target/release/dispatcher
```

`DISPATCH_TOKEN_VARS` lists **environment variable names**, not secret values.
The credentials are existing service tokens, not a new authentication system.
The default names are AGENT_TOKEN_1,AGENT_TOKEN_2. At most 64 distinct nonempty
credentials may be configured. Startup discovers each credential to verify owner,
agent class and policy, then uses the first credential for a single refresh loop.
Each credential verification is counted as a discovery request. Startup fails
closed if any verification fails; later refresh failures pause new attempts
until valid policy returns. No permission from the refresh token is transferred
to another token's job: every operation uses the job's original Bearer token and
the gateway checks its permissions again.

Give each agent the same `DISPATCH_SOCKET` path and **its own** service token in
`AGENT_TOKEN_1`. Agents need neither the dispatcher's other tokens nor its list
of configured credential names. Tokens must never go in command arguments, task
IDs or LLM prompts. Do not print the environment or local request frames.

```sh
# Set DISPATCH_SOCKET to the path printed/selected in the dispatcher terminal.
python3 examples/dispatch_client.py search '{"query":"boots"}'
python3 examples/dispatch_client.py product '{"id":2}'
```

The Python connector can be called from an async agent:

```python
import time
from examples.dispatch_client import DispatcherClient

# Capture this when the logical task originally arrives, before other waiting.
deadline_unix_ms = time.time() * 1000 + 10_000
result = await DispatcherClient().call(
    "search", {"query": "boots"}, deadline_unix_ms=deadline_unix_ms
)
if result["ok"]:
    products = result["body"]
else:
    # Report the failure; never automatically replay an unknown execution.
    error_code, execution = result["code"], result["execution"]
```

Without an explicit deadline, the connector uses ten seconds starting at entry
to `call`. Pass the original absolute deadline if the task arrived earlier.
The original request credential is forwarded over the private local socket and
then sent only as the gateway Bearer header. It is not logged or returned.
Unix socket access trusts the local OS user: the parent directory must be private
(no group/other permissions), the socket is mode 0600, and existing paths are
never silently replaced. Same-user processes are in the same trust boundary.
This implementation requires Unix (Linux/macOS), not native Windows.

Ctrl+C stops accepting, cancels waiting jobs and drains already-started HTTP
attempts before removing the socket. A crash/SIGTERM may leave a stale socket:
ensure that your instance has stopped before removing its private directory.
Never remove another instance's socket or stop unrelated processes. A recreated
instance does not recover old jobs. `SERVICE_URL` selects the gateway origin;
without it, localhost `GATEWAY_PORT` defaults to 3000.
`DISPATCH_TRACE_PATH` optionally creates a fresh local diagnostic JSONL file;
IDs, statuses and policy state are recorded, never credentials or parameters.

## Local wire contract

One Unix-stream connection carries exactly one newline-terminated JSON request
and one newline-terminated JSON reply. The agent keeps the write side open while
waiting; EOF/half-close or extra bytes cancel the request. Request/reply frames
are limited to 64 KiB; initial frame reading has a two-second timeout. Supported
operations are the names in startup discovery, with the same parameter objects
as the service. The route table is the union of registered credentials' permitted
descriptors, but every call retains its own token and gateway authorization.
Legacy path aliases such as `search` and `product` remain supported. No agent
URL/path can bypass this table; newly configured routes require a restart. Local frames are not service HTTP and must never be sent to an LLM.

```json
{"id":"unique-correlation-id","token":"<own-service-token>","operation":"product","params":{"id":2},"deadline_unix_ms":1800000010000}
```

`deadline_unix_ms` is an absolute Unix timestamp, no more than one hour ahead.
At acceptance the dispatcher converts its remaining duration to a monotonic
local deadline. Reading, queueing, retries and backoff do not reset it. Same-host
wall clocks align producer deadlines; large clock steps during submission are
not handled by a clock-synchronization protocol. IDs are correlation labels,
not idempotency keys or durable deduplication records.

Replies contain `ok`, `code`, `execution`, `attempts`, `status` and `body`.
Successful results have code/execution `completed`, final HTTP status and JSON
body. `attempts` counts actual gateway HTTP attempts, excluding discovery and
local RPC. It is null when the connector loses the reply and cannot know the
count. No connector automatically repeats a submission or falls back to direct
HTTP. Do not wrap this connector in another automatic retry loop for unknown
results.

| Result code | Execution | Meaning |
|---|---|---|
| `dispatcher_queue_full` | `not_started` | Local capacity refused the job before dispatch |
| `unregistered_credential` / `invalid_dispatch_request` | `not_started` | No gateway operation was sent |
| `dispatcher_unavailable` | `not_started` | Connector could not establish the local connection |
| `dispatcher_response_lost` | `unknown` | No trustworthy complete local reply; operation may have run |
| `task_deadline` | `not_started` or `unknown` | Deadline reached; unknown if an attempt may already have executed |
| `network_error` | `unknown` | Gateway transport/body failure; never automatically retried |
| `Operation HTTP status N` | Depends on N | Final HTTP error after the shared retry rules; original status/body retained |
| `dispatcher_response_too_large` | `unknown` | A complete local result could not be delivered within the frame bound |

403 remains terminal and retains status 403. 429 and the documented
503 `queue_timeout` with `execution=not_started` are the only automatically
retryable HTTP outcomes; an arbitrary 503, upstream timeout or network error
is not retried. Every logical task has at most five total HTTP attempts,
including the first, respecting integer Retry-After and the original deadline.
The dispatcher reuses `load/retry.rs`, `load/control.rs` and authenticated HTTP
transport shared with the benchmark client. No new dependencies are required.

## Queue and lifecycle

`DISPATCH_CAPACITY` (1..10000, default 1024) bounds all admitted local connections
and logical jobs, **including active jobs, retries/backoff and frame reading**.
This is a conservative bound on waiting queue size, not additional execution
capacity. Kernel socket backlog is separately bounded by the OS. Excess local
jobs receive an explicit queue-full result and are not silently rerouted.

All HTTP attempts enter one bounded FIFO queue. One consumer acquires from the
owner's Gate before passing the attempt to execution. Retry waits release the
HTTP permit; later attempts rejoin the shared queue. This is FIFO by received
attempt order, not a global ordering of producer task creation times. All jobs
keep their own absolute deadline. A budget decrease preserves acquired attempts
and blocks new acquisitions until active falls below the new ceiling.

Cancellation/deadline classification and dispatch share a mutex-protected
transition. If cancellation wins, no attempt is sent. If dispatch wins, loss of
reply is conservatively unknown. A dropped waiting receiver is skipped. A
started attempt retains its Gate permit through full HTTP response-body reading,
even after client disconnect or task deadline; it does not start another retry
for the vanished job. Gateway transport timeout remains separate (30 seconds).
After a transport failure, remote execution can still be unknown; the gateway's
own accounting remains authoritative.

## Limits and verification

Only clients connected to **this instance** share its Gate. Multiple dispatcher
instances, direct clients and clients on other machines still compete. This is
a single point of failure; queued/running task recovery after restart is not
guaranteed. There are no distributed locks, server slot leases, durable queue,
new identity system, unknown-operation replay or new gateway scheduling policy.
A gateway revision reset may require restarting the dispatcher, as with the
existing v3 clients' monotonic-revision checks.

```sh
cargo test --locked --all-targets
python3 -m unittest discover -s tests -v
python3 examples/dispatcher_demo.py
python3 benchmarks/dispatcher/run.py --output benchmarks/results/new-dispatcher
```

The demo uses private servers, temporary credentials and a restricted token;
forbidden search returns 403 and never reaches upstream while product succeeds.
The comparison runs four independent C clients versus the same four producers
through shared S, with identical owner traffic, credentials within each pair,
operations, deadlines, retries and upstream. See the
[report](../benchmarks/DISPATCHER_REPORT.md) and
[methodology](../benchmarks/dispatcher/README.md).
