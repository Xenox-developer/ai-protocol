# Load tests

For four producers sharing one local dispatcher, see the
[C/S methodology](dispatcher/README.md), [report](DISPATCHER_REPORT.md), and
[results](results/dispatcher-main/SUMMARY.md).

For independent processes sharing one owner budget, see the
[1/2/4-client methodology](multiclient/README.md),
[separate report](MULTICLIENT_REPORT.md), and
[raw-run summary](results/multiclient-main/SUMMARY.md).

Stage 4.1 isolates refreshing policy from fixed initial concurrency: see
[methodology](stage4_1/README.md), [report](STAGE4_1_REPORT.md), and
[new C/D results](results/stage4_1-main/SUMMARY.md).

The original controlled experiment is documented in [stage 4 methodology](stage4/README.md).
See [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md) for actual measurements and limitations,
and [raw-run summary](results/stage4-main/SUMMARY.md) for all repeated runs.
It compares compliant A/B clients with the adaptive C client; the older load
generators below are retained as demonstrations and are not used for that comparison.

Both generators first send interactive traffic at 5 requests/s
for 10 seconds, then send interactive traffic at 5 requests/s
and agent traffic at 120 requests/s concurrently for another 10 seconds.
They wait for all tasks to finish and report successful requests, errors,
and the p95 latency of successful tasks.

First, start the catalog and server using the [setup instructions](../README.md#getting-started).
Run the generators one at a time from the repository root:

```sh
source .env.demo
cargo run --locked --release --bin load
cargo run --locked --release --bin load_plain
```

- `load` authenticates, polls v3 policy using one background task, and adjusts
  active agent HTTP attempts to `limits.max_outstanding`. It makes at most five
  attempts total on 429 or the documented 503 queue_timeout/not_started response,
  respecting Retry-After, and releases capacity during retry waits.
  Discovery failures pause new agent attempts until a successful refresh.
- `load_plain` authenticates but sends requests without discovery, a client-side concurrency limiter, or retries.

Both default workloads require `AGENT_TOKEN_1` and `INTERACTIVE_TOKEN`; the server assigns
classes from those credentials. They reject redirects to avoid forwarding credentials.
The owner budget is shared with any other clients using that owner. This is not
a controlled A/B/C protocol comparison, and `load_plain` does not honor Retry-After.

Both clients send `POST /search` with `{"query":""}`. Task duration
includes client-side waiting and retries, but excludes delays in starting
relative to the schedule. The p95 value covers only successful tasks:
compare it alongside the error count.

## Dynamic-budget demonstration

```sh
cargo build --locked --bins
.venv/bin/python examples/dynamic_demo.py
```

This separate scenario starts private servers on free ports and an agent-only
60-task batch, changes the owner budget 5 -> 2 -> 5 using a separate admin token,
and verifies client adaptation and completion. Catalog requests are deliberately
gated/paced, not representative production operations. No paid API is used.
It displays revision, limit, sampled server outstanding, and current active
client HTTP requests. The two counts are sampled at different points in time.

For your own instance, `LOAD_AGENT_TASKS=60 cargo run --locked --bin load` runs
an agent-only batch requiring only `AGENT_TOKEN_1`. The client has a default
120-second overall deadline (`LOAD_DEADLINE_SECS`) and handles Ctrl+C. Failure
to recover policy, incomplete work at the deadline, or failed tasks produce a
nonzero exit. The plain client is unchanged and is not this adaptive demo.

## Queue correctness demonstration

```sh
cargo build --locked --bins
.venv/bin/python examples/queue_demo.py
```

The current scheduler uses weighted 3:1 selection, not the historical strict
priority. The demo completes 80 interactive and 120 agent tasks, expires a
separate queued task without invoking upstream, and checks final outstanding zero.
It uses private instances, free ports, and temporary credentials. Virtual-clock
Rust tests establish selection order and deadline races; the demo does not measure
CPU shares or prove a performance advantage.

## Saved results

- `results/shared/run-1.txt` … `run-5.txt` — the previous setup with a shared queue.
- `results/priority/run-1.txt` … `run-5.txt` — the previous setup with interactive priority.

These files were moved from the local experiment without changing their contents.
They describe the old synthetic `/work` operation with a delay of approximately
100 ms, not the current catalog. The current clients and server do not reproduce
that setup; the files do not record the exact conditions or hardware details.
Keep historical results separate from new `/search` measurements.

To save a new run from the repository root:

```sh
mkdir -p benchmarks/results/catalog
cargo run --locked --release --bin load 2>&1 | tee benchmarks/results/catalog/load.txt
```
