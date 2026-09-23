# Independent clients sharing one owner budget

This experiment compares C (refreshing policy) and D (fixed initial policy) with
1, 2, and 4 independent agent OS processes. Each receives a distinct temporary
credential for `demo-owner`. Every process uses the **whole** published owner
limit in its own Gate. There is no shared Gate, IPC, lease, allocation, task
migration, or retry redistribution. The launcher only assigns work before startup,
starts processes, changes the server budget on schedule, and collects logs.

## Controlled inputs

The input is the exact `demo-owner` subset of each saved stage-4 schedule.
Other-owner agent tasks are excluded, not relabelled. The owner's aggregate
arrivals, IDs, parameters, operation mix and deadlines remain identical for every
process count and mode. Agent tasks are assigned round-robin, preserving each
process's arrival order. Interactive traffic runs in one separate, unchanged
background process in every configuration, including the one-agent baseline.

| Scenario | Agent tasks / rate | Interactive tasks / rate | Arrival window | Agent limit |
|---|---|---|---|---|
| mixed_overload | 240 / 40 per second | 90 / 15 per second | 6 seconds | 5 |
| dynamic | 180 / 22.5 per second | 80 / 10 per second | 8 seconds | 5, then 2 at 2s, then 5 at 5s |

The prior two-owner experiment is **not** a direct numerical baseline. Compare
only the fresh runs in this series. Inputs come from stage4_1/config.json and
stage4-main/*-schedule.json; their hashes and the filtered schedules are saved.
The `agent_rps` values in the inherited scenario configuration describe the
original two-owner source; the effective single-owner rates are the table above.

Unchanged settings: weighted 3:1 class selection, ten server executors, owner
admission limits, interactive budget 10, queue deadlines 2000/10000 ms,
200 ms asynchronous local upstream, ten-second task deadline from planned
arrival, five total operation attempts, exact safe-retry statuses and Retry-After,
22-second global run deadline, 12-second cleanup deadline and 50 ms telemetry.
Each process has the same 1024 pending-task guard; no partition can reach it.
No LLM or paid API is involved. All servers use loopback ephemeral ports and
fresh in-memory credentials. Only child process handles created by the run are
stopped. Credential values are absent from configuration, manifests and logs.

C and D use the same binary, local queue, Gate and retry helper. C polls the
advertised policy interval; D stops discovery after its first valid policy.
Only owners with tasks in that process are discovered. The interactive-only
process performs no discovery. Server changes only add optional token aliases
AGENT_TOKEN_3/4 pointing to the existing owner budget; scheduling, budget and
upstream behavior are unchanged.

There are three fresh repetitions per scenario/process-count/mode: 36 runs.
C/D order alternates for successive pairs. Process-count order rotates by
repetition: 1/2/4, 2/4/1, 4/1/2. All workers share a planned start epoch;
there is no runtime barrier or coordination of requests. Each Tokio process
uses four worker threads, so process and thread overhead grows with count.

## Measurements and checks

The existing analyzer reconstructs logical tasks from scheduled arrival, including
all local waiting and retry pauses. Success, failed and unfinished outcomes stay
in the denominator; p50/p95/p99 cover successful tasks only. Raw task records
retain failed-task elapsed times too. Working HTTP attempts and discovery are
counted separately, with startup/cleanup discovery probes listed separately.
Actual upstream starts/finishes exclude warmup. Admin requests are not discovery.

Each run retains per-process input, PID/token-variable manifest (no secret values),
raw events, a timestamp-merged event stream with client IDs, reconstructed tasks,
server atomic admissions, sampled queue/budget series, upstream/admin events,
cleanup confirmation, per-client completion counts/shares and final error reasons.
The completion distribution reports successes in stable client-ID order; failed
and unfinished counts are also available per process.

The analyzer verifies a disjoint exact partition, distinct PIDs and credential
roles, exactly one arrival/terminal event per task, no client migration, at most
five attempts, and Retry-After pauses on only documented retryable statuses.
Every C process must observe both dynamic revisions; every D process must keep
revision 1 / limit 5 after one discovery. Application delays use each client's
own events and the recorded admin request/response interval, not the first
client to update. Initial/reduced/restored arrival cohorts and window activity
are reported separately, as in stage 4.1.

Every new server admission must have atomic outstanding <= current limit.
Previously accepted tasks above a newly reduced limit are explicitly allowed.
Final local active, server outstanding, queues and running jobs must all be zero;
actual upstream starts and completions must balance. A final partially written
optional telemetry sample may be counted and ignored, as in the existing strict
analyzer; interior or admission corruption is never ignored. Raw data is preserved.

## Reproduce

From the repository root, with the existing Python requirements installed:

```sh
cargo fmt --check
cargo check --locked --all-targets
cargo test --locked --all-targets
cargo build --locked --release --bins
python3 -m unittest discover -s tests -v
python3 benchmarks/stage4/check.py
python3 benchmarks/multiclient/run.py --output benchmarks/results/new-multiclient
python3 benchmarks/multiclient/analyze_clients.py benchmarks/results/new-multiclient
```

Choose a fresh output directory. Analysis can be repeated without making requests.
The separate report is [MULTICLIENT_REPORT.md](../MULTICLIENT_REPORT.md).
This homogeneous local I/O experiment, with three repeats, is not a production
capacity estimate or a statistical significance claim. It tests client collisions,
not per-token fairness or CPU shares. No mechanism for distributing owner slots
is implemented.
