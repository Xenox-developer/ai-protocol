# Stage 4 benchmark report

Measured locally on 2026-09-21. This experiment separates global FIFO versus
weighted 3:1 selection (A/B) from the additional effect of policy adaptation (B/C).
All modes use the same compliant retry helper and scheduled inputs.

## Evidence and environment

The primary series contains **36 sequential runs**: four scenarios, three modes,
three repetitions, rotating ABC/BCA/CAB. It scheduled **11412 logical tasks**.
The [methodology](stage4/README.md) specifies workloads, seed, limits, deadlines,
warmup, memory bounds and measurement semantics. The fixed upstream delay is
200 ms (asynchronous I/O waiting, not CPU or database simulation).

Machine: Apple M1 Pro, MacBookPro18,1, 10 logical CPUs, 16 GiB RAM,
Darwin 24.6.0, arm64. Tools: Rust 1.98.1, Cargo 1.98.1, Python 3.12.1,
HTTPX 0.28.1. Binaries use release optimization and four Tokio worker threads
per Rust process. The machine was not isolated from unrelated operating-system
or user activity. Builds and tests were not run during the main measurements.

Git base: `13fdee82f8a6c9deda5eebd59adb36d4e004653c`, **dirty checkout** including
stages 1–4. No commit/push was made. [Environment metadata](results/stage4-main/environment.json)
records exact tools, configuration, dirty status and source/binary SHA256 hashes;
the base commit alone does not reproduce these uncommitted changes.

A preliminary 1 ms upstream pilot used 120 arrivals/s, above the main peak of
95/s. All 240 tasks per mode succeeded with exactly one attempt, no generator
rejections and zero final outstanding. Pilot p99 arrival lag ranged from 10.4 to
17.3 ms; successful p95 was 11.8–27.5 ms. This supports headroom at the tested rate,
but dispatch timing is not exact, and the pilot does not establish arbitrary
throughput capacity. [Pilot evidence](results/stage4-pilot/SUMMARY.md).

An initial series used a pending-future guard of 256, too close to the calculated
~270-task overload backlog. One preliminary C run actually rejected 27 tasks
at this guard. The guard alone was raised to 1024, and **all** main scenarios and
repetitions were restarted. Schedules, budgets, delays and retry/deadline rules
were unchanged. The [excluded preliminary data](results/stage4-preflight-256/EXCLUDED.md)
remain available and are not pooled with the primary series.

## Latency and success

Each latency below is the **median of three per-run successful-task percentiles**,
in milliseconds, using nearest rank in each run. These are not pooled/global
percentiles. Success counts/fractions pool the three repetitions of that row.
Every latency is measured from planned arrival, including local waiting, retries,
Retry-After, server queueing and upstream. Lower successful-only latency can coexist
with many failures; compare the adjacent success fractions.

| Scenario | Mode | Interactive success | Agent success | Interactive median run p50 / p95 / p99 (ms) | Agent median run p50 / p95 / p99 (ms) |
|---|---|---|---|---|---|
| interactive_only | A | 270/270 (100.00%) | n/a | 206.5 / 214.7 / 222.9 | n/a / n/a / n/a |
| interactive_only | B | 270/270 (100.00%) | n/a | 205.7 / 210.9 / 216.3 | n/a / n/a / n/a |
| interactive_only | C | 270/270 (100.00%) | n/a | 206.9 / 214.7 / 218.1 | n/a / n/a / n/a |
| mixed_low | A | 144/144 (100.00%) | 360/360 (100.00%) | 206.7 / 211.4 / 219.3 | 206.1 / 213.0 / 231.8 |
| mixed_low | B | 144/144 (100.00%) | 360/360 (100.00%) | 207.0 / 212.9 / 215.3 | 206.9 / 214.9 / 218.4 |
| mixed_low | C | 144/144 (100.00%) | 360/360 (100.00%) | 207.3 / 212.0 / 221.9 | 206.5 / 212.0 / 216.8 |
| mixed_overload | A | 270/270 (100.00%) | 1043/1440 (72.43%) | 269.7 / 337.6 / 352.3 | 1210.4 / 4223.9 / 4349.7 |
| mixed_overload | B | 270/270 (100.00%) | 1043/1440 (72.43%) | 232.7 / 311.5 / 323.6 | 1253.4 / 4223.7 / 4342.1 |
| mixed_overload | C | 270/270 (100.00%) | 1428/1440 (99.17%) | 238.6 / 322.9 / 332.3 | 2912.6 / 8306.8 / 9596.6 |
| dynamic | A | 240/240 (100.00%) | 979/1080 (90.65%) | 213.1 / 314.1 / 336.9 | 1205.9 / 4218.2 / 4259.1 |
| dynamic | B | 240/240 (100.00%) | 988/1080 (91.48%) | 211.8 / 235.0 / 242.5 | 1206.4 / 4218.7 / 4305.7 |
| dynamic | C | 240/240 (100.00%) | 1080/1080 (100.00%) | 210.2 / 236.0 / 253.6 | 1413.6 / 5366.2 / 7159.1 |

## Attempts, errors and actual work

Counts below pool three repetitions. All terminal failures in the main series
were agent failures; all interactive tasks succeeded. Failed counts include
exhausted retries and task deadlines. All runs have zero unfinished tasks,
zero generator-capacity rejections, zero queue_timeout, zero network errors,
and zero upstream HTTP errors. These zero counts do not establish robustness
to upstream failures; those paths are covered by separate tests.

| Scenario | Mode | Final failures / unfinished | HTTP attempts / logical tasks | Attempts/task | 429 | Actual upstream starts | Agent batch duration (median ms)* |
|---|---|---|---|---|---|---|---|
| interactive_only | A | 0 / 0 | 270 / 270 | 1.000 | 0 | 270 | n/a |
| interactive_only | B | 0 / 0 | 270 / 270 | 1.000 | 0 | 270 | n/a |
| interactive_only | C | 0 / 0 | 270 / 270 | 1.000 | 0 | 270 | n/a |
| mixed_low | A | 0 / 0 | 504 / 504 | 1.000 | 0 | 504 | 6161.1 |
| mixed_low | B | 0 / 0 | 504 / 504 | 1.000 | 0 | 504 | 6167.0 |
| mixed_low | C | 0 / 0 | 504 / 504 | 1.000 | 0 | 504 | 6157.5 |
| mixed_overload | A | 397 / 0 | 4787 / 1710 | 2.799 | 3474 | 1313 | n/a |
| mixed_overload | B | 397 / 0 | 4833 / 1710 | 2.826 | 3520 | 1313 | n/a |
| mixed_overload | C | 12 / 0 | 1717 / 1710 | 1.004 | 14 | 1702 | n/a |
| dynamic | A | 101 / 0 | 2818 / 1320 | 2.135 | 1599 | 1219 | n/a |
| dynamic | B | 92 / 0 | 2803 / 1320 | 2.123 | 1575 | 1228 | n/a |
| dynamic | C | 0 / 0 | 1393 / 1320 | 1.055 | 73 | 1320 | 10545.4 |

*Batch duration starts at the first planned agent arrival and ends at the last
successful agent completion. It is reported only if **every agent succeeded in
each of the three runs**. n/a never means an incomplete batch finished quickly.
See per-run JSON for individual outcomes and completion times.

In overload C, 12 tasks hit their ten-second task deadline. The other main-series
failures (987) exhausted five permitted attempts after 429. Four C upstream calls
completed after their logical tasks had already failed/cancelled locally: actual
upstream starts exceeded successful tasks by four. This is why logical tasks,
HTTP attempts and actual upstream work have separate counters; cancellation does
not prove that an accepted server operation stopped.

## A -> B: scheduler effect

- Interactive-only and mixed-low scenarios had 100% success in every mode and
  approximately 211–215 ms median run interactive p95. Differences of a few
  milliseconds are small relative to observed dispatch jitter; no benefit is
  established below capacity.
- Under mixed overload, weighted selection reduced median run interactive p95
  from **337.6 to 311.5 ms** (about 7.7%). Agent successes were identical at
  **1043/1440 (72.43%)**; agent 429 increased slightly, 3474 -> 3520. Successful
  agent p95 stayed near 4.22 seconds. This is an observed interactive latency
  difference, not a universal gain in capacity.
- With scheduled budget changes, interactive p95 fell **314.1 -> 235.0 ms**
  (about 25.2%). Agent success changed **979/1080 -> 988/1080**; this modest
  difference should not be overinterpreted with only three repetitions.

## B -> C: additional client adaptation effect

- Under low load, both clients completed everything with one HTTP attempt per
  logical task. This workload shows essentially no additional coordination benefit.
- Under overload, agent success increased **72.43% -> 99.17%** and 429 declined
  **3520 -> 14**. Agent attempts/task fell **3.169 -> 1.005**. The tradeoff is
  waiting: successful agent p95 increased **4223.7 -> 8306.8 ms**. C still had
  12 deadline failures and did not successfully finish the entire agent set.
  Interactive p95 was **311.5 -> 322.9 ms**; there is no additional interactive
  latency improvement in these measurements.
- During dynamic budgets, agent success increased **91.48% -> 100%**, with
  **1575 -> 73** responses of 429. Successful agent p95 increased
  **4218.7 -> 5366.2 ms**. C completed the whole agent set in a median of
  **10545.4 ms** from its first planned arrival. A/B did not complete their
  entire sets successfully, so they have no comparable full-success batch time.
  Interactive p95 was essentially unchanged, **235.0 -> 236.0 ms**.

The adaptive client waits locally before consuming an HTTP attempt. A/B can
exhaust their common five-attempt allowance earlier. Both use the same ten-second
logical deadline and retry rules; adaptation changes where waiting happens,
and which tasks survive long enough to complete. A smaller successful-only p95
for A/B is not evidence that they finished the complete offered workload faster.

## Resource and timing checks

All 36 runs ended with zero outstanding, zero active executors and empty queues.
All measured upstream starts had completed by cleanup. Per-admission snapshots
showed **zero violations** of the current owner limit at acquisition. There were
22 periodic samples with outstanding above a reduced limit; this is expected
for previously accepted work, not a violation. Maximum sampled executors was ten;
maximum sampled class queues were two interactive and four agent jobs. Configured
caps remain 32 per class. Short peaks can occur between the 50 ms samples.

Two owners received the same scheduled 5 -> 2 -> 5 changes in every dynamic run.
Maximum recorded administrative response completion lag was 28.4 ms relative to
the target instant. Main-run per-class p99 arrival lag reached 38.0 ms, with no
memory-guard rejection. Timing noise can affect the small scheduler differences;
we do not claim that the generator delivers perfectly spaced requests.

One final, unterminated diagnostic sample in `interactive_only-r2-B/server.jsonl`
was interrupted by process termination. Raw bytes are preserved. The analyzer
counts this explicitly, tolerates only an incomplete final sample, and still fails
on interior damage, incomplete admission records, or damaged client/upstream data.
The preceding complete sample and cleanup policy snapshots show a drained server.
All task/attempt records are intact. Initial aggregation failed on this tail;
it was fixed and rebuilt from the original evidence without rerunning traffic.
[analysis-version.json](results/stage4-main/analysis-version.json) records the
post-processing code hash separately from the acquisition-time source hashes.

## Verification and reproduction

Passed: Rust formatting, locked all-target checking, debug/release binary builds,
36 Rust test executions (26 server, 8 load-client, 2 shared retry tests also built
into bench_client), and 17 Python tests. Tests verify global FIFO even for equal
clock times; pre-first-attempt waiting in end-to-end; retry accounting; retained
failures/unfinished tasks; and explicit handling of incomplete diagnostics.
The isolated benchmark integration check forces two generator rejections and one
unfinished task, then verifies zero outstanding. Existing catalog smoke, dynamic
budget demo and queue-deadline demo are also rechecked.

```sh
cargo fmt --check
cargo check --locked --all-targets
cargo build --locked --bins
cargo build --locked --release --bins
cargo test --locked --all-targets
python3 -m unittest discover -s tests -v
python3 -m compileall -q examples tests benchmarks/stage4
python3 benchmarks/stage4/check.py
python3 tests/smoke.py
python3 examples/dynamic_demo.py
python3 examples/queue_demo.py
python3 benchmarks/stage4/run.py --pilot --output benchmarks/results/new-pilot
python3 benchmarks/stage4/run.py --output benchmarks/results/new-main
python3 benchmarks/stage4/analyze.py benchmarks/results/new-main
```

## Artifacts and limits of inference

- [Configuration and seed](stage4/config.json), copied into each result root.
- [Primary per-run summary](results/stage4-main/SUMMARY.md), [JSON metrics](results/stage4-main/summary.json),
  and [separate A/B, B/C comparisons](results/stage4-main/comparisons.json).
- Every run directory contains scheduled inputs, raw client/server/upstream/admin
  events, one reconstructed JSONL record per logical task, resource timeseries CSV,
  cleanup evidence and process logs. Credentials and full environment contents
  were not saved. No paid API was called; no user server was stopped.

This is a short local experiment with homogeneous fixed-delay I/O, two agent
owners, read operations, three repetitions and no confidence intervals. Owner
budgets keep server queues short; the experiment does not exercise their maximum
capacity or force queue timeouts. Sampling and synchronous diagnostic writes add
overhead equally across modes. The host is not isolated. These results support
scenario-specific observations about admission failures, waiting and selection,
not production CPU/database performance, precise 3:1 resource shares, or a general
claim that client cooperation makes every request faster. No UI, AIP, replicas,
authorization replacement, commit or push was added.
