# Stage 4: reproducible scheduling and cooperation comparison

## Questions and controlled modes

- A: one global acceptance-order FIFO, no policy adaptation.
- B: weighted 3:1 selection, no policy adaptation.
- C: the same weighted selection, with the existing adaptive owner limiter.

A/B isolates scheduler selection; B/C isolates client adaptation. All three use
`bench_client`, the same schedule file, HTTP stack, five-attempt retry helper,
Retry-After handling, operation timeout, and global run deadline. A/B are compliant
retrying clients, **not** the historical non-retrying `load_plain` baseline.
C imports `src/bin/load/control.rs`; it polls each agent owner once per advertised
refresh interval, pauses on policy failures, and releases active-attempt capacity
before retry waits. Interactive sending is identical in A/B/C. C's discovery
requests are an intrinsic cost of adaptation; they do not consume operation slots.

The gateway defaults to `SCHEDULER_MODE=weighted`; explicit `SCHEDULER_MODE=fifo`
merges class queue fronts using a unique admission sequence assigned under the
queue lock. Equal clock timestamps cannot reorder admissions. It still enforces
32 waiting jobs **per class**, the same deadlines (2000/10000 ms), authentication,
permissions, owner budgets, and ten total executors. There is no third larger FIFO
capacity. Wire policy stays v3. No endpoint or additional identity system is added.

## Reproduction

Install the repository Python requirements, then run from its root:

```sh
cargo build --locked --release --bins
python3 benchmarks/stage4/check.py
python3 benchmarks/stage4/run.py --pilot --output benchmarks/results/my-pilot
python3 benchmarks/stage4/run.py --output benchmarks/results/my-stage4
python3 benchmarks/stage4/analyze.py benchmarks/results/my-stage4
```

Output directories must be new; existing evidence is never overwritten by the
runner. The analyzer deliberately rebuilds derived task files and summaries from
raw evidence. The integration check uses a temporary directory. Each run creates
private gateway/upstream instances on free loopback ports and random credentials
in process memory only. Cleanup stops only those processes. No LLM or paid API.

Read the pilot results **before** drawing conclusions from main runs. The pilot
uses 120 arrivals/s and a 1 ms upstream delay for two seconds in each mode. Compare
arrival lag with the main inter-arrival spacing and 200 ms service delay; check
no generator-capacity rejections and near one HTTP attempt per task. This probes
headroom in this setup, not arbitrary generator throughput. Main runs also record
arrival lag. Pause other heavy workloads where practical; no CPU isolation or
machine exclusivity is assumed.

## Workload and duration

`config.json` is the fixed input, including seed 20260921. Schedules are generated
once per scenario, saved as JSON, and reused byte-for-byte across modes/repetitions.
They contain task ID, planned arrival, class, owner, operation and parameters.
IDs are unique within a schedule; run directories supply mode/repetition scope.
The same ID denotes the same scheduled input across compared runs.
Arrivals follow independent class rates with seeded sub-millisecond jitter;
operations are approximately 70% search and 30% product. Agent tasks alternate
two existing owners, `demo-owner` and `other-owner`, each initially budgeted five.
Their combined ten slots can occupy all executors. Interactive uses its separate
ten-slot budget. Inputs are synthetic and do not depend on responses.

| Scenario | Arrival duration | Interactive/s | Agents/s (both owners) | Changes |
|---|---|---|---|---|
| interactive_only | 6 s | 15 | 0 | none |
| mixed_low | 6 s | 8 | 20 | none |
| mixed_overload | 6 s | 15 | 80 | none |
| dynamic | 8 s | 10 | 45 | both agent owners: 5 -> 2 at 2 s, 2 -> 5 at 5 s |

The deterministic upstream uses a fixed 200 ms `asyncio.sleep` for either
operation. Ten executors imply an idealized upper bound of 50 calls/s before
HTTP/runtime overhead. This models delayed I/O only, not CPU saturation, database
contention, production latency distributions, or LLM processing. Upstream logs
every started and completed call separately from logical tasks and HTTP attempts.

Three repetitions per scenario use mode orders ABC, BCA, CAB. All runs are
sequential. Each starts fresh processes, warms up one request per identity/class
scope, drains, then waits the same 100 ms plus a 500 ms scheduling lead. C may
learn initial policy during the lead. Administrative updates target the same
planned instants in all modes; actual request completion times and returned
snapshots are recorded in `admin.jsonl`, because cross-process timing is not exact.

Each logical task has ten seconds from **planned arrival**, including all client
waiting and retries. The measurement window has a hard 22-second bound from the
common start; remaining tasks are unfinished, never omitted. Startup is bounded
separately; after cancellation cleanup allows up to 12 seconds for accepted
server work to drain. A failed drain invalidates the run instead of claiming zero.
The operating-system process watchdog allows three extra seconds for client exit.

Generation is open-loop: a response does not trigger or delay the next arrival.
The client admits at most 1024 pending logical-task futures; excess arrivals get
explicit `failed/generator_capacity` records and stay in the denominator. Input
is capped at 100000 tasks, 32 MiB, and 10000 maximum pending tasks. An invalid or
oversized configuration aborts before traffic. No per-task loop runs indefinitely.

## Measurements and accounting

`client-events.jsonl` records arrivals, HTTP attempt starts/results, terminal
outcomes, policy polls and run termination. `tasks.jsonl` reconstructs one row per
scheduled task, including all attempts. End-to-end time is final completion minus
**scheduled** arrival; it includes generator lag, local admission waiting, server
queueing, upstream, retries and Retry-After. Cancelled in-flight attempts have no
fabricated HTTP status. Missing terminal records remain unfinished.

`summary.json` provides p50/p95/p99 using nearest rank on **successful logical
tasks only**, alongside successful/failed/unfinished counts and fractions. It
also contains attempts/task, 429, exact queue_timeout, network and upstream errors,
capacity rejections, p99 generator arrival lag, and actual upstream start/end
counts. An agent batch time is reported only if every agent task succeeded;
otherwise it is null. Task deadline failures are final errors; global-deadline
cancellations are unfinished. Retry attempts never increase logical task counts.

If termination interrupts the final server sample, the analyzer preserves the
raw file and explicitly counts that incomplete diagnostic tail. Only an
unterminated final sample is tolerated; damaged admission, client/upstream records
or interior corruption fail analysis. The last complete sample and cleanup
snapshots must both show a drained server. `analysis-version.json` identifies the
post-processing implementation separately from acquisition-time source hashes.

`SUMMARY.md` lists each run. `comparisons.json` separately compares A/B and B/C
using medians across repeated runs. A median of per-run p95 values is explicitly
labelled as such; it is not a pooled or global p95. Three repetitions are modest
and do not establish statistical significance.

For diagnostics, `BENCHMARK_TRACE_PATH=/absolute/new/file.jsonl` enables optional
server telemetry. It records a 50 ms sampled series of both queue lengths,
active executors, and each owner/class's limit/revision/outstanding. It also logs
every admission with the limit and count captured atomically at acquisition.
After shrinking, outstanding above the new limit is legitimate; the checker
instead verifies every **new admission** was within its own atomic limit.
Queue lengths and budgets in periodic samples are not an atomic snapshot across
all owners/completions. Short peaks can fall between samples. `timeseries.csv`
aligns samples with the common wall-clock start; latency uses monotonic clocks.

Telemetry is off unless configured. Local synchronous file writes introduce
measurement overhead, enabled equally in A/B/C. It never logs tokens, headers,
request bodies or full environment contents. `environment.json` records commit,
dirty status, source/binary SHA256 hashes, tool versions, machine characteristics,
configuration and worker count (four Tokio workers per Rust process). Changes in
wall time during a run could distort the cross-process timeline; local task
latencies/deadlines remain monotonic.

## Limits of inference

The saved configuration was chosen to cover below-capacity, overload and
budget changes, not to ensure a particular winner. Adaptive waiting can reduce
429 while increasing latency. Scheduling effects can be small when budgets keep
queues short. Retry-After can synchronize retries. No jitter is added to any mode.
Fixed delays and two principals do not represent heterogeneous real workloads.
Do not use successful-only latency to hide failures, or extrapolate this local
experiment into a general performance or marketing claim.
