# Independent clients sharing one owner: measured report

Measured on 2026-09-22; 36 fresh runs on the uncommitted checkout based on `cce8a6394c8df4262b0a48b2ba0a5cd42e41e0ea`.

The reduction in refusals from refreshing policy was clear with one client, but did not survive as a general benefit with independent clients sharing one owner. In dynamic runs C succeeded on 477/540 tasks with two processes versus D's 524/540; with four processes results were close (517/540 versus 522/540). This is evidence of a coordination gap, not a failure of server budget enforcement.

## Configuration and scope

Three repetitions for every C/D × 1/2/4-agent-process × constant/dynamic configuration. C/D order alternates, process-count order rotates by repetition. Every process has a separate temporary token for `demo-owner`, its own local queue and Gate, and no communication with other clients. C refreshes policy; D freezes its first valid policy. The server publishes the full shared owner limit to each token, never a per-process share.

The source is the saved stage-4 `demo-owner` subset: 240 agent tasks over six seconds (40/s) for constant overload, and 180 over eight seconds (22.5/s) for dynamic 5 → 2 → 5 at 2s/5s. Round-robin partitioning divides these exact tasks across processes without multiplying traffic. One separate interactive process retains the original 90/80 background tasks in every run. Other-owner traffic is excluded. The inherited configuration's agent_rps describes the original two-owner source, not the filtered effective rate. Old two-owner results are not the comparison group.

The 200 ms asynchronous upstream, ten executors, weighted 3:1 scheduler, admission accounting, queue waits 2000/10000 ms, five-attempt safe retry rules, Retry-After, ten-second logical deadline from planned arrival, 22-second global deadline, 12-second cleanup and 50 ms samples are preserved. The pending guard is 1024 per process and never binds. Four Tokio workers per process means process/thread overhead grows with client count.

Only optional credential aliases AGENT_TOKEN_3/4 were added to the server; both refer to the existing owner budget. Budget, scheduler, queue, upstream and retry implementations are unchanged. The benchmark client discovers only owners assigned to it. The existing harness now supports independent child processes and merges their events after completion; this is orchestration, not runtime slot coordination.

Full configuration, environment/source/binary hashes and exact schedules: [raw result directory](results/multiclient-main/). Methodology and commands: [multiclient/README.md](multiclient/README.md). Previous reports and results are preserved.

## Outcomes and request traffic

Counts are sums across three runs. p95 is the median of three per-run successful-task p95 values, in milliseconds, not a pooled percentile. End-to-end latency begins at planned arrival and includes local waiting and retries. Working requests below are agent HTTP attempts; interactive requests and actual upstream starts are stated separately.

| Scenario | Processes | Mode | Agent successes | Success % | Median run p95 ms | Agent requests | 429 | Discovery | Actual upstream, both classes |
|---|---|---|---|---|---|---|---|---|---|
| mixed_overload | 1 | C | 720/720 | 100.00 | 5604.5 | 720 | 0 | 33 | 990 |
| mixed_overload | 1 | D | 720/720 | 100.00 | 5719.0 | 720 | 0 | 3 | 990 |
| mixed_overload | 2 | C | 631/720 | 87.64 | 4223.4 | 1964 | 1333 | 63 | 901 |
| mixed_overload | 2 | D | 616/720 | 85.56 | 4418.5 | 1929 | 1313 | 6 | 886 |
| mixed_overload | 4 | C | 621/720 | 86.25 | 4222.1 | 1993 | 1372 | 126 | 891 |
| mixed_overload | 4 | D | 608/720 | 84.44 | 4224.0 | 1948 | 1340 | 12 | 878 |
| dynamic | 1 | C | 540/540 | 100.00 | 4243.9 | 563 | 23 | 33 | 780 |
| dynamic | 1 | D | 540/540 | 100.00 | 3722.7 | 789 | 249 | 3 | 780 |
| dynamic | 2 | C | 477/540 | 88.33 | 3915.9 | 1145 | 668 | 72 | 717 |
| dynamic | 2 | D | 524/540 | 97.04 | 4217.3 | 1146 | 622 | 6 | 764 |
| dynamic | 4 | C | 517/540 | 95.74 | 4217.8 | 1127 | 610 | 130 | 757 |
| dynamic | 4 | D | 522/540 | 96.67 | 4214.0 | 1133 | 611 | 12 | 762 |

Constant budget: both single-process modes completed all 720 tasks without 429; refreshing offered no refusal reduction. Multiple processes caused many refusals and final failures. C had small numerical success gains over D in the aggregates, but also more 429/discovery traffic. There was no policy change to exploit; three repetitions do not establish an adaptation advantage. Even unchanged-policy refreshes notify Gate waiters, so timing/order of local wakeups can differ.

Dynamic budget: with one process C reduced 429 from 249 to 23 while both completed all 540 tasks. Agent requests plus discovery fell from 792 to 596 (24.7%), but successful-task p95 increased from 3722.7 to 4243.9 ms. With two processes C had fewer successes in every pair, more 429 (668 versus 622), and more agent-plus-discovery requests (1217 versus 1152). With four processes refusal counts were almost identical (610 versus 611), C had five fewer successes, and more requests including discovery (1257 versus 1145). No useful adaptation advantage was established for these independent multi-client configurations.

Lower successful-task latency with multiple processes is not an overall improvement: tasks that exhaust retries are excluded from latency percentiles. See [every run](results/multiclient-main/SUMMARY.md) for p50/p95/p99 and outcomes, and [paired differences](results/multiclient-main/paired-differences.json) for C-minus-D comparisons.

All 10,620 logical tasks have terminal outcomes: 10,096 successes and 524 failures, zero unfinished. All 3060 interactive tasks succeeded in one attempt; every failure was an agent task exhausting five attempts with `Operation HTTP status 429` (274 C, 250 D). There were no task-deadline failures, generator rejections, queue timeouts, network errors or upstream HTTP errors. There were 18237 working HTTP attempts and 499 client discovery requests (457 C, 42 D); all discovery completed successfully. An additional 108 harness startup/cleanup discovery attempts and 36 admin PATCH requests are separate from those counts. Actual upstream starts and completions both total 10,096, excluding 72 warmup calls.

## Completion distribution between clients

Vectors below sum successful completions across the three repetitions, in stable client-ID order. Every client receives equal task counts: 720/N for constant and 540/N for dynamic. Per-run counts, success fractions, completion shares, p50/p95/p99, requests, discovery and failure reasons remain in summary.json; the raw-run table shows every individual vector. These measurements do not promise per-token fairness.

| Scenario | Processes | C completed by client | D completed by client |
|---|---|---|---|
| mixed_overload | 1 | 720 | 720 |
| mixed_overload | 2 | 313, 318 | 310, 306 |
| mixed_overload | 4 | 153, 153, 157, 158 | 151, 159, 148, 150 |
| dynamic | 1 | 540 | 540 |
| dynamic | 2 | 242, 235 | 262, 262 |
| dynamic | 4 | 129, 129, 129, 130 | 133, 125, 134, 130 |

## Policy application and recovery

Every C process applied revision 2 / limit 2 and revision 3 / limit 5; every D process made exactly one discovery, remaining at revision 1 / limit 5. Table entries are median lower..upper observed application-delay bounds, in ms, across clients and repetitions. Bounds use admin request start/response completion and the client application log. They include clock-alignment/logging uncertainty; exact server mutation time is not instrumented.

| Processes | Shrink delay ms | Restore delay ms | Reduced-window 429 C / D | Restored-window 429 C / D |
|---|---|---|---|---|
| 1 | 528.1..531.2 | 539.2..544.4 | 23 / 249 | 0 / 0 |
| 2 | 524.9..529.9 | 532.7..539.0 | 217 / 243 | 451 / 379 |
| 4 | 521.6..526.0 | 540.5..542.5 | 237 / 245 | 373 / 366 |

These are activity windows [2s,5s) and [5s,end); they include retries of earlier arrivals. JSON separately retains full eventual outcomes and latency for initial/reduced/restored arrival cohorts. A task arriving during the reduced budget can finish after restoration without changing cohorts. With multiple processes collisions continue after restoration; observing the latest revision does not reserve capacity against other clients.

## Collisions and possible remedies — not implemented

Each independent Gate permits up to L active attempts locally, while the server admits only L queued-plus-running tasks for the owner. With N clients their nominal local ceilings sum to N×L. Published outstanding is a snapshot, not a reservation. Simultaneous or overlapping clients can each be below their own ceiling and still collide at the shared admission check. The server correctly rejects excess work with 429 before queueing. Finite retries then turn some collisions into final failures. Identical Retry-After delays can also synchronize later contention; this experiment does not isolate the causal contribution of each timing effect.

- Static per-client shares could keep the sum within the owner budget, but require known membership and a rule for changes/failures. With budget 2 and four clients, giving each even one slot is invalid; rounding all shares down gives zero and requires explicit rotation/allocation. Idle shares can waste capacity.
- A shared client-side limiter or coordinator could acquire from one owner pool, but introduces IPC/distributed state, failure handling and an availability dependency. It is outside the no-coordination experiment.
- Server-issued leases or explicit capacity grants could assign slots with authoritative ownership, but require expiry, release, crash recovery and a defined fairness policy. That is a new protocol mechanism, not simply another discovery field.
- Desynchronized retry timing might reduce synchronized collisions but cannot enforce a common client-side bound or solve slot allocation. Retry strategy was left unchanged here.

No allocation mechanism, new fairness policy, production identity service or cross-process coordination was implemented.

## Verification and evidence limits

Every run passed exact-disjoint-partition and fixed-input checks, distinct process/credential-role checks, one arrival/terminal per task, no task migration, five-attempt and Retry-After audits, per-client C/D revision checks, atomic owner admission checks, and final cleanup. All 36 runs have zero admission violations and zero final owner outstanding, queued jobs, running jobs and local active attempts. Maximum sampled running jobs was ten. Thirty-nine samples had old accepted outstanding above a newly reduced limit; these are valid, and no new over-budget admission occurred.

One optional final telemetry sample was interrupted at shutdown in `dynamic-n2-r2-C`. The existing analyzer explicitly counted this trailing incomplete diagnostic record and retained its raw bytes; no admission, client or upstream record was discarded. The preceding complete sample and explicit policy cleanup confirm zero outstanding. No measured run was discarded or retuned.

Final checks passed: `cargo fmt --check`, `cargo check --locked --all-targets`, `cargo test --locked --all-targets` (38 test executions: 27 server, 8 load client, 3 benchmark), `cargo build --locked --release --bins`, `python3 -m unittest discover -s tests -v` (23 tests), the existing `benchmarks/stage4/check.py` bounded-generator/deadline integration check, Python 3.10 syntax checks, and a fresh reanalysis of all 36 runs. An earlier temporary integration-check invocation failed at client startup; the unchanged isolated repeat and final invocation passed. It was not part of the measurement series.

Three repeats on one ordinary local machine with a homogeneous asynchronous I/O upstream do not establish statistical significance or general production performance. The host was not isolated; maximum observed per-run/class p99 arrival lag was 83.6 ms and stays included in task latency. Process overhead and local wakeup timing vary; per-client fairness, real network/upstream failures and heterogeneous task durations were not evaluated. Existing deterministic tests cover lifecycle/error races; this successful-upstream experiment does not re-prove them by load alone.

No paid APIs, user-server shutdowns, commits or pushes were performed. Ports were ephemeral, credentials temporary and confined to child environments. Previous stage-4/4.1 result directories were preserved.

## Reproduce

```sh
cargo build --locked --release --bins
python3 benchmarks/multiclient/run.py --output benchmarks/results/new-multiclient
python3 benchmarks/multiclient/analyze_clients.py benchmarks/results/new-multiclient
```

Use a fresh output directory. Full validation commands and artifact definitions are in [the methodology](multiclient/README.md). Acquisition source hashes and later analysis hashes are stored separately; the final analyzer also verifies worker input files and produces paired differences.
