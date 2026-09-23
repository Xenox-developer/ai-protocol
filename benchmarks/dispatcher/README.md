# Independent C versus shared local dispatcher S

This comparison repeats the four-process experiment with new data on both sides.
C has four independent adaptive Gates/pollers; S connects the same four producer
processes to one local dispatcher Gate, attempt queue and refresh loop. One fixed
interactive background process continues calling the gateway directly.

Inputs are copied byte-for-byte from `results/multiclient-main` schedules:
240 agent + 90 interactive tasks for constant overload (6 seconds), and
180 + 80 for dynamic 5 -> 2 -> 5 (8 seconds, changes at 2s and 5s). Round-robin
assignment, token roles, operation mix and task IDs are identical. Token values
are freshly generated in memory for each pair and **reused across its C/S runs**,
then discarded; no credentials appear in artifacts. Load is not multiplied.

The inherited 200 ms asynchronous upstream, ten gateway executors, weighted
3:1 scheduler, budget 5 / dynamic 2, interactive budget 10, 2000/10000 ms queue
waits, five attempts, Retry-After, ten-second planned-arrival task deadline,
22-second run bound, 12-second cleanup and 50 ms telemetry are unchanged. The
local dispatcher capacity is the existing 1024 pending guard, including active
jobs and retry waits. It never binds in this workload. No LLM/paid API is used.

Three new paired repetitions per scenario produce twelve runs, alternating
CS/SC/CS then SC/CS/SC. Each run starts private gateway/upstream instances on
free loopback ports, and S adds a Unix socket in a private temporary directory.
Only processes created by the harness are stopped. Warmup is identical. Each
Tokio process uses four threads; S adds process/IPC overhead. The dispatcher
verifies each token once at startup before the common scheduled start, then
polls through one credential. Startup verification is included in discovery
counts; it is not four independent refresh loops.

## Evidence

The existing task reconstruction and owner-admission analyzer are reused. All
latency starts at planned arrival, including producer scheduling, socket transfer,
shared queue waiting, operation attempts and backoff. Successful-only p50/p95/p99
must be read with success/failure/unfinished fractions. Actual elapsed times of
failed tasks remain in task JSONL. Working requests are actual gateway HTTP
attempts, not producer-to-dispatcher messages. Discovery counts include initial
credential verification and refresh, reported separately from working requests
and from harness readiness/cleanup probes and admin PATCH requests.

C retains producer event files. S additionally records dispatcher events with
monotonic timestamps anchored to a wall-clock epoch. The harness merges them
with producer events, attributes each actual HTTP attempt to its originating
task/client and marks its executor as dispatcher. Original raw files remain
available. A task's original token is used for every attempt; the real permission
demo checks a restricted token separately without changing the primary workload.

Analysis verifies exact task partitions, unchanged per-worker configuration,
per-client C refresh, a single S policy stream applying both dynamic revisions,
identity verification of all four tokens, five-attempt/retry-pause bounds,
atomic new gateway admissions within the current owner budget, upstream drain
and final outstanding/local active zero. Previously accepted work above a
reduced ceiling is permitted. Zero 429 is not required: stale policy and other
owner traffic may cause legitimate refusals.

Results include per-run summaries, task/HTTP/dispatcher/upstream/admin JSONL,
budget CSVs, environment and source/binary hashes, aggregate totals and paired
S-minus-C differences. No new gateway telemetry or scheduling behavior is added.
The architecture comparison includes a shared FIFO attempt queue; independent
Gate waiters do not promise FIFO, so this does not isolate coordination from every
possible change in local wakeup order. Three repeats on a local, unisolated host
are not a statistical-significance or production-capacity claim.

## Reproduce

From the repository root with the existing Python requirements installed:

```sh
cargo fmt --check
cargo check --locked --all-targets
cargo test --locked --all-targets
cargo build --locked --release --bins
python3 -m unittest discover -s tests -v
python3 examples/dispatcher_demo.py
python3 benchmarks/stage4/check.py
python3 benchmarks/dispatcher/run.py --output benchmarks/results/new-dispatcher
python3 benchmarks/dispatcher/analyze_dispatcher.py benchmarks/results/new-dispatcher
```

Use a fresh output directory. Agent setup and local error semantics are in
[docs/dispatcher.md](../../docs/dispatcher.md). Measured findings are in the
[separate report](../DISPATCHER_REPORT.md). Previous comparison results are kept.
