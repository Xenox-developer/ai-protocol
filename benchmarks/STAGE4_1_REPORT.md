# Stage 4.1: fixed initial concurrency versus refreshing policy

Measured on 2026-09-22. This report compares **new C and new D runs**. None of the
old C observations is used as a comparison group. Old stage-4 evidence is unchanged.

## Conclusion

Refreshing policy did not demonstrate an additional benefit with constant budgets.
D completed more agent tasks in this small sample and made only its initial two
policy requests per run. Both modes still had deadline failures under overload.

During 5 -> 2 -> 5 changes, both clients completed **100%** of agent tasks. C reduced
429 responses from **579 to 73**, and operation-plus-discovery traffic from
**1905 to 1459** requests across the three runs (23.4% fewer). This is a reduction
in rejected HTTP work, not an improvement in completion rate. Successful agent
median run p95 was **5.674 s for C versus 5.315 s for D**; full-batch completion
was also slightly later for C. Thus the fixed limiter was sufficient for successful
completion in this chosen dynamic workload, while refreshing reduced refusals.

These are scenario-specific measurements, not statistical proof of a universal
winner. The load and upstream were not tuned to favor C.

## Configuration and equivalence

The twelve sequential runs cover two existing scenarios, three new repetitions
per mode, in orders CD/DC/CD for overload and DC/CD/DC for dynamic. The runner
copies the original schedule JSON byte-for-byte and verifies all workload settings.
All per-run task lists match those schedules. Two agent owners start at five slots
each; interactive has a separate ten-slot budget. Both use the same server with
3:1 selection, ten executors, existing class queue caps and 2000/10000 ms queue
waiting limits. The server binary's SHA256 matches the stage-4 acquisition binary.

C and D share the same Rust `Gate`, local waiting mechanism, active-attempt
accounting, HTTP code, five-attempt retry helper, Retry-After rules and ten-second
logical deadline measured from scheduled arrival. D exits its policy-reading task
after the first valid policy for each owner. C continues refreshing. Initial
policy recovery is identical. No server or Gate/retry implementation was changed.

The source [configuration](stage4_1/config.json) retains seed 20260921, 200 ms
asynchronous upstream delay, 1024 pending-task guard, 22-second run bound and
12-second cleanup bound. Overload offers 15 interactive/s and 80 agent/s for six
seconds; dynamic offers 10 interactive/s and 45 agent/s for eight seconds. Both
agent owners change at nominal 2 s and 5 s. The same warmup and 500 ms scheduling
lead are used for every run. This is delayed I/O, not a CPU or database model.

Environment: Apple M1 Pro / MacBookPro18,1, ten logical CPUs, 16 GiB RAM, Darwin
24.6.0 arm64; Rust/Cargo 1.98.1, Python 3.12.1, HTTPX 0.28.1. Rust uses release
binaries and four Tokio workers per process. Base commit is
`cce8a6394c8df4262b0a48b2ba0a5cd42e41e0ea`, with uncommitted stage-4.1 changes.
[Acquisition metadata](results/stage4_1-main/environment.json) stores exact source,
schedule and binary hashes. Builds/tests were not run during measurements; the
host was not otherwise isolated from user/OS activity.

## Latency and task outcomes

Counts/fractions below pool the three runs for the given scenario/mode.
Every percentile shown is the **median of three per-run successful-task
percentiles**, in milliseconds. It is not a pooled/global percentile. End-to-end
starts at scheduled arrival and includes waiting before the first attempt, retries,
Retry-After, server queueing and upstream. Failed tasks remain in the denominator.

| Scenario | Mode | Interactive success | Agent success | Agent final failures | Interactive p50 / p95 / p99 ms | Agent p50 / p95 / p99 ms |
|---|---|---|---|---|---|---|
| mixed_overload | C | 270/270 (100.00%) | 1410/1440 (97.92%) | 30 | 236.4 / 302.8 / 318.5 | 2890.7 / 8289.0 / 9503.0 |
| mixed_overload | D | 270/270 (100.00%) | 1427/1440 (99.10%) | 13 | 228.8 / 324.9 / 340.6 | 2984.3 / 7990.0 / 9457.4 |
| dynamic | C | 240/240 (100.00%) | 1080/1080 (100.00%) | 0 | 208.6 / 228.9 / 243.8 | 1287.3 / 5674.4 / 7137.2 |
| dynamic | D | 240/240 (100.00%) | 1080/1080 (100.00%) | 0 | 209.6 / 243.8 / 250.6 | 1209.5 / 5315.4 / 7284.3 |

All **6060** logical tasks were accounted for: **6017 successful, 43 final errors,
zero unfinished**. All 43 errors are agent `task_deadline` in overload: 30 C and
13 D. None exhausted its five attempts with a final 429. Both dynamic modes have
zero final errors. There were no generator rejections, queue timeouts, network
errors or upstream HTTP errors in this series.

Neither overloaded agent set has an all-success completion time; reporting only
its successful tail as a completed set would be misleading. In dynamic runs,
all-success agent-batch time (first planned arrival to last completion) has median
**10454.2 ms for C** and **10357.6 ms for D**.

## HTTP attempts, discovery and actual execution

These counts pool three runs. Discovery is separate from working HTTP attempts.
The attempts/task columns below apply to agents only; all interactive tasks use
one attempt. Harness readiness/cleanup probes are counted separately, not mixed
with workload-client discovery.

| Scenario | Mode | Agent attempts/task | Working attempts (both classes) | Client discovery | Working + discovery | 429 | Actual upstream starts/completions |
|---|---|---|---|---|---|---|---|
| mixed_overload | C | 1.060 | 1797 | 84 | 1881 | 110 | 1687 |
| mixed_overload | D | 1.012 | 1727 | 6 | 1733 | 28 | 1699 |
| dynamic | C | 1.068 | 1393 | 66 | 1459 | 73 | 1320 |
| dynamic | D | 1.536 | 1899 | 6 | 1905 | 579 | 1320 |

All 162 client discovery requests succeeded; none was incomplete. D used exactly
two per run, one per owner. Harness lifecycle attempts total 53 (including startup
connection attempts); their per-run startup/cleanup counts are in each summary.
They occur outside measured task activity and are excluded from the table above.

There were **6026 actual upstream calls**, all completed by cleanup, versus 6017
successful logical tasks. Nine calls belong to logical tasks that hit their local
deadline (seven C, two D). The server correctly retained those accepted jobs until
completion. Overload 429 responses first appeared after task-deadline failures in
every run that had any 429. This is consistent with local permits being released
on cancellation while the gateway still counts accepted work; client concurrency
is not a reservation or proof of downstream cancellation.

## Policy application

The real-run checks confirm that D ends at limit five/revision one for both
owners, with only one valid policy applied per owner. C observes revision two
(limit two) and revision three (limit five) in every dynamic run. All final
client active-attempt counters are zero.

No gateway timestamp was added. The limit mutation lies between the logged admin
request start and response completion. Delay bounds use the first C application
log for the matching owner and revision. Across six observations per transition:

| Change | Median lower..upper delay (ms) | Minimum lower..maximum upper (ms) |
|---|---|---|
| 5 -> 2 | 518.1..524.7 | 515.7..528.6 |
| 2 -> 5 | 533.9..535.9 | 526.5..542.9 |

This is roughly half a second in this polling phase, not a general maximum.
It includes application logging and cross-process clock-alignment uncertainty.
D has null application delays: retaining its initial numeric five is not applying
the later revision three. Maximum admin response completion lag from the planned
change instant was 10.2 ms. Raw request and response boundaries are retained.

At restoration, D already allows five local active attempts. C remains at two
until the next refresh, about 534 ms after the change in this series. Refresh
latency therefore also delays use of newly available capacity; updating policy
is not an unqualified latency improvement.

## Reduced-budget and recovery results

This first table groups agents by **planned arrival**: initial [0,2s), reduced
[2,5s), restored [5s,end). Their complete eventual results remain in that cohort,
even when a reduced-window task finishes after recovery. Counts pool three runs;
p95 is the median of the successful cohort p95 values from those runs.

| Arrival cohort | Mode | Agent success | Agent p95 ms | Agent attempts | Agent 429 across all its retries |
|---|---|---|---|---|---|
| initial | C | 270/270 | 4072.9 | 295 | 25 |
| initial | D | 270/270 | 3753.4 | 342 | 72 |
| reduced | C | 405/405 | 6557.7 | 453 | 48 |
| reduced | D | 405/405 | 6295.5 | 909 | 504 |
| restored | C | 405/405 | 4310.2 | 405 | 0 |
| restored | D | 405/405 | 3954.9 | 408 | 3 |

This second table counts **events occurring in a window**, for both classes,
regardless of when the task arrived. It should not be confused with the cohort
table. In particular, after-restoration work includes the accumulated backlog.
Nominal window boundaries are 2/5 s; actual admin timing is available separately.

| Activity window | Mode | Working attempt starts | 429 responses | Client discovery starts | Upstream starts |
|---|---|---|---|---|---|
| initial | C | 305 | 0 | 12 | 299 |
| initial | D | 306 | 0 | 0 | 300 |
| reduced | C | 331 | 73 | 18 | 264 |
| reduced | D | 822 | 575 | 0 | 252 |
| restored | C | 757 | 0 | 30 | 757 |
| restored | D | 771 | 4 | 0 | 768 |

Each dynamic mode also makes six initial discovery requests before time zero
across its three runs. Those are included in total discovery, not in the windows
above. Both reduced and restored arrival cohorts have 100% success for C and D;
there are no hidden late failures assigned to a different phase.

## Correctness, interpretation and limitations

All twelve runs drained to zero outstanding, zero executors and empty queues.
Atomic admission snapshots found **zero violations**. Eighteen periodic samples
had outstanding above a newly reduced ceiling due to previously accepted work;
these are valid. Maximum sampled execution was ten, queues were at most two
interactive and four agent jobs. No diagnostic record was truncated in this series.

With constant budgets, refreshing adds requests without new capacity information.
D's agent success was higher in this sample, but three repetitions do not establish
that D is generally better. The shared Gate does not promise FIFO between waiting
client futures; C's unchanged-policy refresh also wakes waiters. Wake order,
local deadlines and runtime timing can affect which tasks complete. This experiment
does not isolate those mechanisms further or change the Gate to favor either mode.

With dynamic budgets, C's concrete additional benefit is fewer refused working
attempts and lower total client request traffic, even after counting discovery.
It did not increase success above D's 100%, and did not reduce latency or batch
duration in this series. D's fixed limit was sufficient for this workload's
completion objective. Phase-cohort results show the same success/wait tradeoff.

The maximum observed per-class per-run p99 dispatch lag was 10.7 ms. Inputs are
identical but runtime executions are not deterministic. Three paired runs, one
non-isolated local machine, homogeneous delayed I/O and no confidence intervals
limit causal/statistical claims. This is not a CPU/DB benchmark, production advice,
or evidence that all changing workloads behave similarly. No load parameters were
retuned and no new B run was performed; do not use this follow-up to re-estimate
stage-4 B/C differences from non-contemporaneous data.

## Tests, artifacts and reproduction

Passed: `cargo fmt --check`, locked all-target check, release build, **37 Rust test
executions** (26 server, eight load-client, three benchmark including shared retry
checks), and **20 Python tests**. New tests confirm fixed versus updating Gate
state using a real loopback policy endpoint; revision-matched delay bounds; and
full end-to-end outcomes when reduced-budget arrivals finish after restoration.
Existing retry, cancellation, queue, budget and accounting checks are reused.
The original bounded-generator/deadline integration check also passed again.

- [Per-run summary](results/stage4_1-main/SUMMARY.md), [all metrics](results/stage4_1-main/summary.json),
  [paired C-minus-D differences](results/stage4_1-main/paired-differences.json).
- Each run includes raw client/server/upstream/admin JSONL, reconstructed task
  records, CSV resource series and cleanup evidence. Initial discovery and final
  Gate state are explicit. Failure reasons and phase cohorts are retained in JSON.
- [Methodology](stage4_1/README.md), [configuration](stage4_1/config.json),
  [environment and hashes](results/stage4_1-main/environment.json).

From the repository root, with its Python requirements installed:

```sh
cargo fmt --check
cargo check --locked --all-targets
cargo test --locked --all-targets
cargo build --locked --release --bins
python3 -m unittest discover -s tests -v
python3 -m compileall -q examples tests benchmarks/stage4 benchmarks/stage4_1
python3 benchmarks/stage4/check.py
python3 benchmarks/stage4_1/run.py --output benchmarks/results/new-stage4_1
python3 benchmarks/stage4_1/comparison.py benchmarks/results/new-stage4_1
```

All runs used private free ports and temporary credentials, with no paid API and
no user-server interruption. Secrets were not written to results. Old benchmark
data and reports are preserved. No commit or push was performed for this stage.
