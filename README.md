# AI Protocol

An experimental protocol for cooperative agent access to web services over HTTP.
The Rust gateway publishes permitted operations, authenticates callers, enforces
shared owner budgets, selects work with interactive/agent weights of 3:1, and forwards work to a
Python catalog. This is a local MVP, not a public standard or production platform.

## Getting started

You need Rust/Cargo with edition 2024 support and Python 3.10+.
Run commands from the repository root.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cargo build --locked --bins
python examples/setup_demo.py
```

Run credential setup once. It generates six random credentials in `.env.demo`
with mode 0600, refuses to overwrite an existing file, and does not print tokens.
The file is ignored by Git. Never commit it or share its contents.

Start the catalog in the first terminal:

```sh
.venv/bin/python examples/catalog_service.py
```

Start the gateway in the second terminal:

```sh
source .env.demo
cargo run --locked --bin ai-protocol
```

The catalog listens on `127.0.0.1:4000` and the gateway on `127.0.0.1:3000`.
Optional `CATALOG_PORT` and `GATEWAY_PORT` select different local ports; set
`CATALOG_PORT` for both servers and `GATEWAY_PORT` for the gateway and clients.
The gateway requires `AGENT_TOKEN_1`, `AGENT_TOKEN_2`, and `INTERACTIVE_TOKEN`;
it fails closed if required configuration is missing. `PRODUCT_ONLY_TOKEN`
and `OTHER_AGENT_TOKEN` are optional test roles. `ADMIN_TOKEN` enables the
separate administrative endpoint; if absent, that endpoint returns 404. All provided values must differ.
The environment is read at startup; the server does not load `.env.demo` itself.

In a third terminal:

```sh
source .env.demo
.venv/bin/python examples/discover_client.py
```

Choose operation `1` and enter `{"query":"boots"}`, or operation `2` and
`{"id":2}`. An empty query returns all products. To use a language model:

```sh
.venv/bin/python examples/llm_client.py
```

The LLM client retains `gpt-4.1-mini` and executes at most one selected operation.
It reads `OPENAI_API_KEY` or prompts for that key without displaying it. The
service credential from `AGENT_TOKEN_1` is separate and is never included in the
model request. Calling the real model requires OpenAI API access.

The single credential file is a local demonstration convenience: a deployed
agent should receive only its own token, never all demo roles.

## HTTP API and owner budgets

All endpoints require `Authorization: Bearer <token>`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/agent-policy` | Version 3, principal, class, limits, permitted operations |
| POST | `/search` | Substring search: `{"query":"boots"}` |
| POST | `/product` | Retrieve a product: `{"id":2}` |
| PATCH | `/admin/principals/{principal_id}/limits` | Change an agent budget; separate `ADMIN_TOKEN` required |

`AGENT_TOKEN_1`, `AGENT_TOKEN_2`, and `PRODUCT_ONLY_TOKEN` initially share five outstanding
operations for `demo-owner`. This includes queued and running work, across all
connections and client processes. The product-only token cannot search (403).
`OTHER_AGENT_TOKEN` has its own five slots. `INTERACTIVE_TOKEN` has a separate
budget of ten and receives up to three selections before an agent selection when
both queues contain ready work. FIFO is preserved within each class.

Missing/invalid credentials return 401. A full owner budget or queue returns
429 with `Retry-After`, before the operation reaches the catalog. Policy and
other authenticated-route responses use `Cache-Control: no-store`.
`X-Client-Type` is ignored. Full details: [protocol v3](docs/protocol.md).

To test retries, stop the gateway and restart it with the same credentials:

```sh
source .env.demo
TEST_429=1 cargo run --locked --bin ai-protocol
```

The first authorized agent operation receives `429` with `Retry-After: 2`.
Python and cooperative Rust clients make at most five attempts in total. They
retry 429 and only the documented `503 queue_timeout` with
`execution: "not_started"`, respecting Retry-After and their overall deadline.

## Dynamic budgets (stage 2)

Run a self-contained demonstration without an LLM or API key:

```sh
cargo build --locked --bins
.venv/bin/python examples/dynamic_demo.py
```

It creates private gateway/catalog instances on free loopback ports, generates
credentials only in memory, and starts 60 concurrent logical tasks using the
Rust `load` client. Catalog completions are deliberately gated and paced.
It holds five accepted jobs, changes the agent limit to two, observes the client
drain to two active HTTP attempts, then raises the limit to five and observes
increased sending. All 60 tasks must complete, with final outstanding zero.
Only instances started by the demonstration are stopped. No user server is killed.

Output includes policy revision, configured limit, sampled server outstanding,
and current client active HTTP attempts. The server's snapshot and the client's
count are not an atomic cross-process measurement. This proves coordination in
a controlled example, not a performance advantage or a fairness guarantee.

To use an existing gateway, configure a distinct `ADMIN_TOKEN` before starting
it, then send `PATCH /admin/principals/demo-owner/limits` with that credential
and `{"max_outstanding":2}` (later `5`). Limits must be integers from 1 to 1000;
unknown principals return 404. Ordinary service tokens return 403. Interactive
budgets remain ten. Reapplying the same limit does not increment the revision.
Existing `.env.demo` files are preserved: add a separate random `ADMIN_TOKEN`
locally if administrative access is needed; do not replace existing credentials.
Never give the administrative credential to an agent client.

For an agent-only batch against your configured local gateway:

```sh
source .env.demo
LOAD_AGENT_TASKS=60 LOAD_DEADLINE_SECS=120 cargo run --locked --bin load
```

The client uses one policy poller and pauses new agent attempts on refresh errors.
It retries discovery once per second after an error (each request has a two-second
timeout). Already sent requests continue. The default 120-second workload deadline
or Ctrl+C stops the client, including if discovery never recovers. A single local
limiter cannot reserve server budget shared with other clients, so 429 remains valid.

## Weighted service and queue deadlines (stage 3)

When both queues stay nonempty, the scheduler selects at most three interactive
jobs before one agent job. An empty class does not reserve executors or accumulate
credits: the other class can use all ten executors. Selection is FIFO within each
class; running jobs are never preempted. This is a selection-order guarantee,
not a CPU share or an exact response-time guarantee. Different operation durations
can produce different completion rates.

Configure waiting deadlines at gateway startup:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `INTERACTIVE_MAX_WAIT_MS` | 2000 | Interactive waiting limit in milliseconds |
| `AGENT_MAX_WAIT_MS` | 10000 | Agent waiting limit in milliseconds |

Both accept integers from 1 to 3600000. These defaults are initial demo settings,
not production recommendations. `/agent-policy` publishes the authenticated
class's value as `queue.max_wait_ms`, an additive v3 field.

Waiting starts at admission on a monotonic clock and ends at handoff to an
executor. A waiting job at or beyond its deadline is removed, releases its owner
slot, and never reaches the catalog. One scheduler timer handles expiry even
when all executors are busy and no new requests arrive. The separate five-second
upstream timeout applies after selection.

Queue expiry returns HTTP 503, `Retry-After: 1`, and:

```json
{"error":{"code":"queue_timeout","execution":"not_started"}}
```

429 rejects an attempt before admission; queue_timeout removes an accepted attempt
before execution. A retry is a new attempt of the same logical task, reacquires
budget, and receives a new queue deadline. Arbitrary 503, upstream timeouts, and
network errors do not authorize retries. Older v3 clients can ignore the added
policy field, but must update to gain the new safe retry behavior.

Run the isolated demonstration without an LLM:

```sh
cargo build --locked --bins
.venv/bin/python examples/queue_demo.py
```

It uses free loopback ports and temporary credentials, holds all ten executors,
and deliberately expires one waiting job with a configured 500 ms interactive
limit. The catalog records calls to prove that job never arrived. Continuous
streams then complete 80 interactive and 120 agent tasks, and final outstanding
is zero. Only demonstration-owned processes are stopped. This is a correctness
scenario; deterministic selection-order assertions live in the Rust tests.

## Reproducible A/B/C comparison (stage 4)

A separate open-loop benchmark compares global FIFO without adaptation (A),
weighted 3:1 without adaptation (B), and weighted 3:1 with adaptation (C).
All modes share compliant retries, owner budgets, queue limits, deadlines,
authentication, ten executors, and the same predetermined logical task schedule.
Two agent owners can collectively fill all executors. A/B isolates scheduling;
B/C isolates the additional effect of client adaptation.

The default server remains weighted. Set `SCHEDULER_MODE=fifo` explicitly to
select global admission-order FIFO while retaining the separate class queue caps.
Optional `BENCHMARK_TRACE_PATH` enables local diagnostics; it must name a new file.
It is disabled during ordinary operation and never records tokens or request bodies.

```sh
cargo build --locked --release --bins
python3 benchmarks/stage4/check.py
python3 benchmarks/stage4/run.py --pilot --output benchmarks/results/my-pilot
python3 benchmarks/stage4/run.py --output benchmarks/results/my-stage4
```

The runner creates private instances and temporary credentials, performs warmup,
and runs four scenarios three times per mode in rotating ABC/BCA/CAB order.
New arrivals do not wait for responses. Client waiting, safe retries and server
waiting all count toward latency from the scheduled arrival; dropped generator
work and unfinished tasks remain in the results. Fixed upstream delay models I/O,
not CPU or database contention. No LLM or paid API is used.

See [methodology](benchmarks/stage4/README.md),
[measured report](benchmarks/BENCHMARK_REPORT.md), and
[per-run results](benchmarks/results/stage4-main/SUMMARY.md).
Raw JSONL, timeseries CSV, configuration, seed, commit/dirty status, tool and
machine metadata are retained. Successful-only percentiles are always accompanied
by outcome counts; a median of run p95 values is never called an overall p95.

## Checks

```sh
cargo fmt --check
cargo check --locked --all-targets
cargo build --locked --bins
cargo test --locked --all-targets
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q examples tests
```

Rust tests cover HTTP authentication and permissions, class spoofing, concurrent
shared budgets, independent scopes, queue overflow, cancellation, completion,
upstream errors/timeouts, and catalog routing. Dynamic tests also cover atomic resizing/revisions, admin
permissions, polling failure/recovery, and actual HTTP sending. Controlled upstream gates hold
work while assertions run, rather than relying on a fast catalog to overload.
Queue tests use controlled executors and virtual monotonic time to check 3:1
selection, FIFO, capacity use, idle-event expiry, cancellation races, and shrinking
budgets. Retry tests use virtual/fake time. Python tests cover policy validation, bounded retries, credential handling,
origin restrictions, and a mocked model call.

For a complete local smoke test:

```sh
.venv/bin/python tests/smoke.py
```

It selects free loopback ports, starts and stops the real catalog and built gateway,
and uses credentials generated
only in memory, runs the discovery client, checks catalog results and permissions,
and checks a real two-second retry delay. It does not call an external LLM.
See [benchmarks](benchmarks/README.md) for the updated Rust load generators.

## Migration and limitations

**v3 is incompatible with older clients.** Update the server and clients together,
generate credentials, and load them in each terminal. Replace `max_in_flight`
with the v3 owner/class `limits.max_outstanding` contract. Header-based class
selection and unauthenticated discovery are no longer supported.

Budgets apply to one gateway process. Both waiting queues hold at most 32 jobs,
and ten jobs can run in total. Cancelled queued jobs are pruned during admission
and scheduling, including queue deadline wakeups; cleanup is not guaranteed
immediately on every TCP disconnect.
Running work keeps its slot even if its caller disappears. An upstream timeout
does not prove the downstream operation stopped.

Weighted selection does not promise per-owner fairness or real-time deadlines.
Registration/token rotation, cluster coordination, AIP, and production HTTPS
setup are not implemented. The current read-only catalog does not establish
safe retry rules for future writes. Historical benchmarks do not measure v3.

## Structure

- `src/main.rs`: authentication, policy, admission, and scheduler.
- `src/budget.rs`: shared dynamic accounting and job guards.
- `src/queue.rs`: weighted selection, waiting limits, and expiry.
- `src/queue_tests.rs`: deterministic queue and scheduler tests.
- `src/tests.rs` and `src/dynamic_tests.rs`: server tests using controlled upstreams.
- `src/bin/load/`: client limiter, safe retry handling, and adaptation tests.
- `src/bin/`: authenticated cooperative/plain load generators and the controlled benchmark client.
- `src/telemetry.rs`: optional local benchmark admission events and sampled metrics.
- `examples/`: catalog, credential setup, Python clients and shared v3 helper.
- `tests/`: Python unit tests and real catalog smoke test.
- `docs/protocol.md`: current contract; `docs/archive/`: historical contract.
- `benchmarks/`: reproducible stage-4 experiment, report, raw results, and historical measurements.

## License

Copyright (c) 2026 Nikita Chindin. Licensed under [Apache-2.0](LICENSE).
