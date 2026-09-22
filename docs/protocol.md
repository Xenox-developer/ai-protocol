# AI Protocol — experimental v3 contract

Status: a local, single-process prototype over HTTP, not a public standard.
This document describes `src/main.rs` and the current example clients.

## Authentication and permissions

All service routes require `Authorization: Bearer <token>`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/agent-policy` | Discover the authenticated caller's policy |
| POST | `/search` | `search_products`, JSON body `{"query":"boots"}` |
| POST | `/product` | `get_product`, JSON body `{"id":2}` |

Tokens are opaque server-configured credentials. Missing, unknown, malformed,
or duplicate Authorization headers return `401` with `WWW-Authenticate: Bearer`.
Bearer scheme matching is case-insensitive. Credentials never come from JSON
or query parameters. `X-Client-Type` is ignored, including when absent.

Each token maps to a `principal_id`, an `agent` or `interactive` class, allowed
operation names, and a shared budget. The server assigns the class; an
interactive credential does not prove the caller is human. A disallowed operation
returns `403` before body parsing or admission. Discovery filtering alone is
not authorization: direct operation calls are checked again.

The demo configuration uses environment variables:

| Variable | Required | Principal | Class | Allowed operations | Shared budget |
| --- | --- | --- | --- | --- | --- |
| `AGENT_TOKEN_1` | Yes | demo-owner | agent | Both | 5 |
| `AGENT_TOKEN_2` | Yes | demo-owner | agent | Both | Same 5 |
| `PRODUCT_ONLY_TOKEN` | No | demo-owner | agent | get_product | Same 5 |
| `INTERACTIVE_TOKEN` | Yes | demo-owner | interactive | Both | Separate 10 |
| `OTHER_AGENT_TOKEN` | No | other-owner | agent | Both | Separate 5 |

Startup fails on missing required, empty, invalid, or duplicate token values.
There are no default credentials. A separate optional `ADMIN_TOKEN` enables
administrative updates and must differ from every service token. This fixed demo mapping is separate from the
scheduler; registration, rotation, revocation, and external identity adapters
are outside this version.

## Discovery

An authenticated `GET /agent-policy` returns JSON such as:

```json
{
  "version": 3,
  "policy_revision": 1,
  "refresh_after_ms": 1000,
  "outstanding": 0,
  "principal_id": "demo-owner",
  "client_class": "agent",
  "limits": {"scope": "principal", "max_outstanding": 5},
  "queue": {"max_wait_ms": 10000},
  "operations": [
    {
      "name": "get_product",
      "description": "Retrieve a single product by its known numeric ID.",
      "method": "POST",
      "path": "/product",
      "input_schema": {
        "type": "object",
        "properties": {"id": {"type": "integer", "minimum": 0}},
        "required": ["id"],
        "additionalProperties": false
      }
    }
  ]
}
```

This example is for the product-only credential. Other credentials receive both
operations. `operations` contains only permitted descriptors. Each descriptor
includes `name`, `description`, `method`, `path`, and `input_schema`.
All responses passing through authentication, including policy and error
responses, carry `Cache-Control: no-store`. Tokens are never returned.

The exact budget scope is **one gateway process + principal_id + client_class**.
Read `scope: "principal"` together with `client_class` and this single-process
restriction. Connections, client processes, operations, and token count do not
multiply this budget. Discovery does not enqueue catalog work or consume it.
The policy reports a configured ceiling, not currently available slots or a
reservation. `policy_revision` starts at 1 per principal/class and increments
only on an actual limit change. `refresh_after_ms` is 1000 in this demo.
`outstanding` is an advisory snapshot of accepted work, not a reservation.
Revision, limit, and outstanding are read together under one budget lock.
`queue.max_wait_ms` is the maximum accepted waiting time for this caller's class,
not an upstream timeout or estimated latency. It is fixed at process startup,
independent of dynamic budget revisions, and does not change through the admin API.

## Administrative limit updates

`PATCH /admin/principals/{principal_id}/limits` requires a distinct Bearer
`ADMIN_TOKEN`. The environment is read at startup. With no admin token configured,
this route returns 404 and no updates are possible. Missing/unknown credentials
return 401 with `WWW-Authenticate: Bearer`; valid agent/interactive credentials
return 403. An admin token cannot call the ordinary service routes. Duplicate
authorization headers are rejected. Administrative responses also use `no-store`.

Body: `{"max_outstanding":2}`. Only integer values from 1 to 1000 are supported.
Invalid values, types, missing/extra fields return 422; malformed JSON returns
400. Unknown principals return 404, without creating an identity or budget.
Only the existing **agent** budget of the named owner is changed. Its tokens
continue sharing the same counter. Interactive and other owners are unaffected.

A successful response is a coherent snapshot at the update's linearization point:

```json
{
  "principal_id": "demo-owner",
  "client_class": "agent",
  "policy_revision": 2,
  "limits": {"scope":"principal", "max_outstanding":2},
  "outstanding": 5
}
```

Outstanding may legitimately exceed a reduced maximum. Existing accepted jobs
are preserved. Admission rejects new work while outstanding is at or above the
current maximum, so lowering 5 to 2 at outstanding 5 admits nothing new until
outstanding is 1 or 0. Increasing 2 to 5 immediately allows new admissions
subject to queue capacity. Resizing, admission, completion, and snapshots use
the same mutex-protected state; no independent replacement semaphore is created.
An identical PATCH is a no-op for revision. Revisions and limits reset to their
configured defaults on process restart; persistence and a process epoch are not
part of this stage. The wire version remains 3 for every limit change.

## Admission, execution, and cancellation

The outstanding count includes every accepted queued or running operation.
Admission checks and increments the counter without waiting for capacity before
enqueueing, and gives the job an owning guard. Concurrent
requests cannot admit more than the owner/class maximum. Agent budgets start at 5 and are administratively adjustable;
interactive budgets remain 10. Both waiting queues are bounded to 32 jobs; at most
10 jobs execute across both classes and all principals.

If the owner budget is exhausted or the applicable queue is full, the gateway
returns `429` with `Retry-After: 1` before admission. The rejected attempt never
reaches the catalog. Having free owner budget does not guarantee queue space.
`TEST_429=1` produces one artificial rejection of an authorized agent operation
with `Retry-After: 2`; it consumes neither queue space nor owner budget.

The job owns its budget guard. Outstanding is decremented after the upstream attempt
finishes, including reading its response body, or fails. If the HTTP handler
is cancelled while upstream is running, the independent job continues holding
its permit. Dropping the result receiver does not release a running job's slot.

Queued jobs whose result receiver is closed are removed when another request
attempts admission or when the scheduler wakes for selection or a queue deadline. Removing a job releases
its permit. Immediate detection and cleanup of every TCP disconnect is not
promised; HTTP stacks may not cancel a handler immediately. Cancellation after
a job has been selected may still allow it to run. The gateway does not claim
to cancel or track downstream execution after an upstream timeout.

## Weighted selection and maximum queue waiting

The default startup mode is `SCHEDULER_MODE=weighted`, with the rule below.
Explicit `SCHEDULER_MODE=fifo` is available for controlled comparison: select the
oldest accepted job across classes by a unique sequence assigned under the queue
lock. Both separate per-class queue caps, owner budgets, permissions, deadlines,
and the total execution cap remain identical. Invalid mode values fail startup.
Mode is fixed for the process lifetime; the v3 wire contract does not change.

While both classes have ready queued work, select at most three interactive jobs
consecutively before one agent job. The initial cycle starts with interactive.
Within each class, surviving jobs keep FIFO order across all principals. If one
class is empty, the other uses every available executor; such selections reset
the bounded streak rather than accumulating credits. Completed executors are
refilled while any ready work remains. Running work is never preempted and total
execution remains capped at ten.

In weighted mode, the 3:1 rule is a guarantee about **selection order**, not completion order,
upstream HTTP arrival order, CPU shares, or exact response time. Concurrent jobs
can start executing and finish in a different order. Operation durations affect
throughput; there is no per-owner fairness or resource isolation. A job may expire
before its turn, and there is no guarantee of completing every accepted job.

Startup settings `INTERACTIVE_MAX_WAIT_MS` (default 2000) and `AGENT_MAX_WAIT_MS`
(default 10000) accept integers from 1 through 3600000 milliseconds. Invalid
settings fail startup. These are initial demo values, not production advice.

Each job records monotonic time at admission. Its waiting interval ends when the
scheduler removes it for handoff to an executor. At `now >= accepted_at + max_wait`,
a still-waiting job expires. This interval excludes client-side waiting, request
parsing before admission, and execution after handoff. It is independent of the
five-second upstream timeout. Already selected work never receives queue_timeout,
even if it finishes after its former queue deadline.

Removal for expiry and selection share the queue lock and a final deadline check
at handoff. Exactly one path owns the job's budget guard. Expiry removes the job,
releases the guard before notifying its caller, and cannot call the catalog.
Cancellation and expiry cannot release the same slot twice. Lowering the dynamic
budget preserves accepted jobs and their deadlines; expiry releases their slots
against the same counter, even if outstanding temporarily exceeds the new limit.

One scheduler timer targets the nearest waiting deadline and is re-armed on new
admissions. It runs even when every executor is busy and no other event occurs;
there is no busy polling or per-job background loop. Runtime scheduling can delay
the delivery of an expiry response; this is not a real-time response guarantee.

Expiry returns HTTP **503** with integer `Retry-After: 1` and JSON:

```json
{"error":{"code":"queue_timeout","execution":"not_started"}}
```

429 means rejected **before admission**. queue_timeout means accepted, then removed
**before execution**. `execution: "not_started"` is used only for this documented
queue failure, never for an already sent upstream request's network error or timeout.

## Catalog behavior and errors

The gateway calls the local catalog at `127.0.0.1:4000` by default (configurable
with `CATALOG_PORT`; `GATEWAY_PORT` changes the default gateway port 3000), with a five-second
request timeout and without forwarding service credentials:

- `/search` calls `GET /products/search?query=...`. Search is a case-insensitive
  substring match on English names. An empty query returns all three products.
- `/product` calls `GET /products/get?id=...`. IDs are non-negative `u64` values.
  An unknown ID returns `200` with `{"product":null}`.
- JSON requests must match the operation fields; unknown fields are rejected.
- Connection failures, non-200 upstream statuses (including upstream 429), and
  response-body read failures become `502`. A timeout while sending/waiting for
  response headers becomes `504`. A lost result channel can produce `500`.

A gateway admission `429` guarantees that this attempt was not accepted; the
documented queue_timeout response guarantees an accepted attempt never started.
A timeout, network error, `500`, `502`, or `504` does not prove absence of
upstream execution and does not authorize an automatic retry. The catalog
currently performs reads only; idempotency for writes is not defined.

## Clients and retry rules

The current profile uses non-negative integer seconds for `Retry-After`, not
HTTP dates. Wait at least that duration before retrying. If the header is missing
or malformed, the example clients wait one second. Retries have at most five
attempts including the first request, shared across both retryable cases:

- HTTP 429 (rejected before admission).
- HTTP 503 whose JSON `error.code` equals `queue_timeout` **and**
  `error.execution` equals `not_started` (removed before execution).

Missing/wrong markers, malformed bodies, and all other 503 responses are terminal.
Network errors and upstream failures are not retried. Exhaustion is failure.
Retry-After does not reserve capacity. Each retry is a new attempt of the same
logical task, acquires current budget again, and gets a fresh server queue deadline.
No jitter is implemented. The retry rule applies to both classes in cooperative
`load` and to the shared Python execution helper; `load_plain` intentionally has
no retries as a non-cooperative baseline.

Rust retains its overall workload deadline across policy waiting, execution, and
retry delays. The synchronous Python helper has a 120-second logical operation
budget (`execute(..., task_timeout=...)`), checked before and after each HTTP
attempt and before retry sleeps. Each HTTP I/O timeout is capped at the smaller
of 30 seconds and remaining budget. HTTPX timeouts apply per I/O phase, so Python
does not promise hard cancellation at the exact overall deadline during an active
request; it rejects a late result and starts no further attempt. Discovery and
model decision time precede this operation budget.

Python clients validate version 3, principal, class, scope, positive limit,
unique operation names, descriptors, and JSON Schema. Unsupported versions or
invalid policies fail before catalog traffic. Operations must use POST and
simple paths on the configured origin. Redirects and external schema references
are rejected. Service credentials are sent only to that origin; the LLM receives
only the user task and operation descriptions/schemas. The OpenAI credential is
separate from the service token.

Each Python invocation executes at most one operation, so it does not implement
parallel workload control or demonstrate client cooperation under load. Multiple
invocations still share their server-enforced owner budget. The Rust `load`
client has one shared adjustable limiter and one policy polling task. It counts
active HTTP attempts through the full response body, releasing the slot before
any retry delay. Every retry reacquires capacity using the latest policy.
A reduction does not cancel sent requests; new sends wait until active is below
the new maximum. Growth wakes waiting tasks. Policy requests never acquire an
operation slot, so polling continues even when all slots are occupied.

Polling begins immediately and then waits the advertised `refresh_after_ms`
after each successful response. The demo advertises 1000 ms; this client accepts
100..60000 ms and budgets 1..1000. Each policy request times out after two seconds.
On any network, HTTP, parse, or validation error, new agent attempts pause until
a valid policy arrives; ongoing requests finish normally. Refresh retries wait
one second after each failure. A previously learned identity cannot change;
revision rollback or different limits at the same revision are rejected. Restart
the client if the gateway restarts with an older revision. The last server
outstanding shown on a `policy_error` log is stale, retained for diagnostics.

The default overall workload deadline is 120 seconds (`LOAD_DEADLINE_SECS`, range
1..86400); Ctrl+C also stops it. Deadline/interruption abort local tasks and stop
the poller, but do not promise cancellation in the gateway or catalog. A valid
Retry-After longer than 86400 seconds fails the logical task without retrying
rather than retrying too soon. Interactive baseline requests do not depend on
agent policy availability. `LOAD_AGENT_TASKS` (1..10000) selects an agent-only
batch; otherwise the original baseline/mixed workload is retained.

`load_plain` authenticates but does not use discovery or retries. These are demo
generators, not a controlled evaluation of protocol benefits.

## Compatibility and limitations

Stage 3 adds optional-to-clients `queue.max_wait_ms` without changing wire version
or existing v3 field meanings. Existing v3 clients that ignore unknown fields
remain compatible; they may treat queue expiry as terminal until updated to
recognize its exact error markers. Neither client requires the new queue field
to execute against an earlier v3 server.

Stage 2 adds `policy_revision`, `refresh_after_ms`, and `outstanding` without
changing v3 field meanings. Existing v3 Python clients ignore these extra fields
and still perform one operation at a time; their compatibility tests include the
new fields. Earlier fixed-limit v3 clients can continue working, but may receive
429 after a server limit change. They do not gain live adaptation automatically.
The new adaptive load client requires the added dynamic-policy fields and pauses
on a stage-1 server that does not supply them. New owners/classes are not created.

**v3 is incompatible with v1/v2 clients.** Unauthenticated clients receive 401;
`X-Client-Type` no longer selects a class; `max_in_flight` is replaced by
`limits.max_outstanding` with a different, server-enforced scope. `/work` is not
available. Clients must explicitly support v3 and use assigned credentials.

Budgets and queues exist only in memory in one process. Multiple replicas would
have independent counts. AIP, additional scheduling policies, persistent jobs,
registration, and general protection from abusive HTTP traffic are not included.
Separate queues do not isolate CPU, database, or network resources. For remote
use, provide HTTPS and prevent direct access to the catalog that bypasses the
gateway. Never distribute interactive or other owners' credentials to agents.

The optional `BENCHMARK_TRACE_PATH` writes local admission and sampled resource
metrics to a new file. It adds no HTTP endpoint or policy fields and is disabled
by default. See the [controlled comparison methodology](../benchmarks/stage4/README.md).

See [setup and checks](../README.md) and the
[archived synthetic v1 experiment](archive/protocol-v1.md). Historical latency
results are not measurements of this v3 implementation.
