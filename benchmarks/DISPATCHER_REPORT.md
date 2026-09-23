# Shared local dispatcher: measured C/S comparison

Report updated 2026-09-23 (Europe/Moscow). The final series started at 2026-09-22T20:58:44Z. This is uncommitted work on `cce8a6394c8df4262b0a48b2ba0a5cd42e41e0ea`; no commit or push was performed.

The shared dispatcher completed all 1260 agent tasks in the final comparison, versus 1135/1260 for four independent adaptive clients. It substantially reduced refusals and discovery traffic in this workload. This result is limited to clients connected to one dispatcher and does not establish general production performance.

## Implementation and compatibility

- `src/bin/dispatcher.rs` adds one local process for one verified agent owner, one bounded FIFO attempt queue, one shared Gate and one policy refresh loop. All configured tokens are discovered once at startup; differing owners or classes are rejected. Every operation uses its original submitting credential, including limited tokens.
- Existing `load/control.rs` and `load/retry.rs` are reused unchanged. Authenticated HTTP transport is shared with the benchmark client in `src/bin/client/transport.rs`. Mode S sends jobs over a private Unix socket; C retains its independent adaptive client behavior. `examples/dispatch_client.py` is the agent connector.
- The original absolute task deadline includes producer scheduling, socket transfer, dispatcher waiting and backoff. Shrinking the Gate preserves started attempts. A mutex linearizes cancellation/deadline classification with dispatch; a cancelled waiter cannot later be sent while claiming not_started. Started HTTP keeps its permit until its response body completes even after caller loss.
- The admitted-job bound includes frame reading, queued/running jobs and retry waits. Overflow is explicit. Local connection/reply failures never trigger automatic resubmission or direct HTTP fallback. A lost reply has execution unknown and unknown attempt count; an upstream network/body error is terminal, not retried. No durable recovery or unknown-operation replay is implemented.
- Service policy stays v3 with the existing `limits.scope = "principal"` and `limits.max_outstanding`. The documentation explicitly defines the term max_in_flight as the aggregate owner budget, never a process quota; discovery reserves nothing. No field, server scheduling, authorization or budget behavior changed. The gateway release binary hash matches the prior independent-client experiment. No dependency was added.

Connection instructions, local request/reply semantics and shutdown behavior are in [docs/dispatcher.md](../docs/dispatcher.md).

## Controlled comparison

Twelve fresh runs compare C (four independent adaptive clients) with S (the same four producers through one dispatcher), three repetitions per scenario and mode. Pair order is CS/SC/CS for constant overload and SC/CS/SC for dynamic budgets. Both sides use new measurements; old C runs are not reused.

The exact single-owner schedules are copied byte-for-byte from the previous independent-client series: 240 agent tasks at 40/s over six seconds for constant overload; 180 at 22.5/s over eight seconds for dynamic 5 → 2 → 5, changing at 2s/5s. Round-robin assignment to the same four credential roles is unchanged. Temporary token values are identical within each C/S pair, never stored. Interactive background remains a separate fixed process with 90/80 tasks. The owner flow is partitioned, not multiplied; other-owner agent traffic is absent.

The gateway still has ten executors, weighted 3:1 class selection, owner admission accounting, interactive budget 10 and queue waits 2000/10000 ms. The upstream is the same local asynchronous 200 ms I/O model. Five total attempts, exact retryable 429/documented queue-timeout responses, Retry-After, ten-second task deadline from planned arrival, 22-second run deadline, 12-second cleanup and 50 ms server samples remain unchanged. Dispatcher capacity is 1024 admitted jobs and never binds. Each Tokio process has four threads; S adds one process and IPC overhead.

The dispatcher validates four token identities before the common task start, then refreshes with one credential. These identity checks are included in discovery counts. Private loopback ports and a mode-0700 temporary socket directory isolate each run; only child processes created by the harness are stopped.

## Outcomes

Counts below are sums over three runs. p95 is the median of per-run successful-task p95 values in milliseconds, not a pooled percentile. Latency includes all waiting from planned arrival. Working requests are actual agent-to-gateway HTTP attempts, not local submission messages. Actual upstream starts include both classes and exclude warmup.

| Scenario | Mode | Agent success | Success % | Median run p95 ms | Agent requests | 429 | Discovery (identity subset) | Actual upstream |
|---|---|---|---|---|---|---|---|---|
| mixed_overload | C | 619/720 | 85.97 | 4225.5 | 1974 | 1355 | 122 (0) | 889 |
| mixed_overload | S | 720/720 | 100.00 | 3828.6 | 720 | 0 | 45 (12) | 990 |
| dynamic | C | 516/540 | 95.56 | 3414.8 | 1136 | 620 | 134 (0) | 756 |
| dynamic | S | 540/540 | 100.00 | 2193.6 | 562 | 22 | 45 (12) | 780 |

At constant budget S improved success from 619/720 to 720/720 and reduced 429 from 1355 to zero. Agent requests plus discovery fell from 2096 to 765. At dynamic budgets S improved success from 516/540 to 540/540, reducing 429 from 620 to 22 and requests plus discovery from 1270 to 607. S improved successful-task p95 in every paired run as well as the aggregate median. This is a comparison of complete architectures, including one shared FIFO attempt queue; it does not isolate coordination from every difference in local waiter ordering.

Zero 429 is not a correctness requirement. The dynamic S runs received 7, 8 and 7 refusals. A published limit can lag a server change, and unrelated clients of the same owner would compete for the same places. All S tasks completed within the original five-attempt/deadline rules.

All 3540 logical tasks have terminal outcomes: 3415 successes, 125 failures, zero unfinished. Every final failure belongs to C and exhausted five attempts with status 429 (101 constant, 24 dynamic). All 1020 interactive tasks succeeded in one attempt. There were no queue timeouts, task-deadline failures, generator rejections, local dispatcher failures, network errors or upstream HTTP errors in the final measurement series.

Total working HTTP attempts: 5412. Client/dispatcher discovery: 346 (256 C, 90 S). The S total includes 24 initial token-verification requests and 66 requests from the single owner refresh loop. Harness readiness/cleanup adds 36 discovery attempts, and there are 12 admin PATCH requests; these are counted separately. Actual upstream starts and completions both total 3415, excluding 24 warmup calls.

Each S producer completed its assigned 60/60 constant-budget or 45/45 dynamic tasks in every run. C per-client completion counts, attempts and latency are retained in summary.json. Full per-run p50/p95/p99, working/discovery requests and outcomes are in [SUMMARY.md](results/dispatcher-main/SUMMARY.md); raw task records include failed-task elapsed times. [Paired differences](results/dispatcher-main/paired-differences.json) use S minus C. Do not interpret successful-only percentiles without the corresponding failure fraction.

## Policy changes and cleanup

Every C process and the single S owner loop observed both dynamic revisions. In S the median observed application-delay bounds were 508.8..512.0 ms for shrink and 523.9..528.5 ms for restoration. Bounds use the admin request/response interval and client application logging; exact server mutation time is not instrumented. The delay explains why a shared local limiter cannot guarantee zero refusals immediately after a remote decrease.

| Dynamic window | 429 C | 429 S | Arrival-cohort success C | Arrival-cohort success S |
|---|---|---|---|---|
| Reduced [2s,5s) | 241 | 22 | 180/204 | 204/204 |
| Restored [5s,end) | 379 | 0 | 201/201 | 201/201 |

Window activity includes retries of older arrivals; arrival cohorts retain full eventual outcomes even if they finish in a later window. Initial cohorts and all phase error reasons are also preserved in JSON.

All twelve runs passed atomic new-admission checks against the current server budget and ended with zero server outstanding, queue entries and running jobs. S ended with one final Gate state at active zero; C ended with zero active in each producer. Twelve sampled observations above a newly reduced ceiling contain permitted old accepted work; none was a new over-budget admission. Maximum sampled running jobs was nine, below the global limit ten. All upstream calls drained. No final diagnostic sample was truncated in this series.

## Permission demonstration and tests

The reproducible [dispatcher_demo.py](../examples/dispatcher_demo.py) starts private real gateway/upstream/dispatcher processes and four producers, one holding PRODUCT_ONLY_TOKEN. Its search receives terminal 403 after one HTTP attempt; its product request succeeds. The denied search has zero upstream calls. Five of six demonstration tasks succeed, with the intended permission failure and final outstanding zero. Saved evidence: [dispatcher-demo/result.json](results/dispatcher-demo/result.json). The workload comparison keeps four full-operation tokens exactly as before; permission probing is separate.

Final checks:

- `cargo fmt --check` and `cargo check --locked --all-targets`.
- `cargo test --locked --all-targets`: 48 executions (27 gateway, 8 load client, 3 benchmark, 10 dispatcher). Dispatcher coverage includes original-token permissions, mixed-owner rejection, shared shrink/grow, expired/disconnected waiters, running deadline/unknown execution, permit lifetime, capacity refusal, lost reply without replay, one policy loop and cancellation/dispatch ordering using a virtual clock.
- `cargo build --locked --release --bins`.
- `python3 -m unittest discover -s tests -v`: 27 tests, including four connector checks for original credential/deadline, explicit queue-full/unavailable replies, malformed/lost replies and no automatic replay.
- Existing `python3 benchmarks/stage4/check.py`, the real permission demonstration, all twelve comparison runs and `python3 benchmarks/dispatcher/analyze_dispatcher.py benchmarks/results/dispatcher-main`.

The unchanged shared retry tests cover exact virtual-clock Retry-After waits and five-attempt totals for 429 and only documented 503, with network/unsafe errors terminal. Frame and operation deadlines have separate conservative error classification; no unknown operation is retried by the connector.

An earlier development series was interrupted after review found a cancellation/dispatch classification race. Ten complete runs and one partial run are retained in [dispatcher-preflight](results/dispatcher-preflight/) and explicitly excluded. The fix serializes cancellation and dispatch under one mutex and has a regression test. The final twelve runs repeat the identical workload on the corrected binary; no traffic or retry parameter was tuned. The earlier permission demonstration is also retained separately. Prior stage-4, stage-4.1 and independent-client results are preserved.

## Limits

The dispatcher coordinates only connected clients of one owner and one gateway. Other direct clients or dispatcher instances can still contend. It is a single point of failure: restart recovery, persistence, deduplication and automatic recovery of unknown operations are not guaranteed. Correlation IDs are not idempotency keys. No distributed locks, capacity leases, new identity system or gateway fairness changes were introduced.

Unix sockets and same-user OS trust restrict this implementation to local Linux/macOS use. A gateway identity/revision reset may require restarting the dispatcher. Original deadlines use same-host wall-clock timestamps converted to monotonic duration at receipt; clock-step recovery is not provided. Active HTTP can outlive the caller deadline, and a transport failure cannot establish that remote work stopped.

Three repetitions on one unisolated local host with homogeneous 200 ms I/O are not a significance or production-capacity claim. Maximum observed per-run/class p99 producer arrival lag was 34.6 ms; it remains included in task latency. Added process/IPC overhead and differing local queue discipline are part of S. Large catalog bodies, multiple machines, sustained hostile clients, production credential rotation and durable restart semantics were not evaluated.

## Reproduce and inspect

```sh
cargo build --locked --release --bins
python3 examples/dispatcher_demo.py
python3 benchmarks/dispatcher/run.py --output benchmarks/results/new-dispatcher
python3 benchmarks/dispatcher/analyze_dispatcher.py benchmarks/results/new-dispatcher
```

Use a fresh output directory. See [methodology](dispatcher/README.md) for complete validation commands, [agent instructions](../docs/dispatcher.md) for connection and failure handling, and [raw/derived results](results/dispatcher-main/) for configurations, schedules, source/binary hashes, separate producer/dispatcher events, task records, budget CSVs and upstream/admin evidence. No paid API, user-server shutdown, commit or push was used.
