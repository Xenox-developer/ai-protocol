# Stage 4.1: refreshing versus fixed initial policy

This follow-up isolates the benefit of policy updates from the benefit of waiting
locally before sending. It compares **new C and new D runs**, not D against old C
measurements. Stage-4 artifacts are retained unchanged.

## Only substantive difference

Both modes use the same weighted gateway, authentication, owner budgets, queue
limits and deadlines, ten executors, fixed-delay upstream, scheduled inputs,
Rust client, `Gate`, local waiting, retry helper and logical task deadlines.
Neither adds a new queue discipline or a different retry strategy.

- C discovers policy and keeps polling once per advertised refresh interval.
- D uses the same initial discovery path, freezes the first valid limit/revision
  for each agent owner, then exits its policy-reading task. It does not rediscover
  after a 429. Its ordinary operation retries still obey Retry-After.

If initial discovery fails, both modes leave the Gate paused and retry discovery
with the same one-second delay until valid policy or the run deadline. All
operation attempts, including retries, acquire the same Gate and release it
before waiting for Retry-After. The shared maximum is five attempts; the task
budget is ten seconds from **planned arrival**. Interactive sending is unchanged.
C's extra discovery traffic and its pause on refresh failures are intrinsic to
refreshing; they are not copied artificially into D.

## Configuration and sequence

`config.json` copies the unchanged stage-4 settings: seed 20260921, 200 ms delayed
I/O upstream, 2000/10000 ms server waiting deadlines, 1024 pending-client-task guard,
22-second run bound, 12-second cleanup bound, and four Tokio workers per process.
It selects only the existing mixed_overload and dynamic scenarios. The runner
rejects changed workload settings and copies saved stage-4 schedule files exactly,
recording SHA256 hashes. It does not regenerate or tune traffic.

- mixed_overload: six seconds, 15 interactive/s and 80 agent/s total.
- dynamic: eight seconds, 10 interactive/s and 45 agent/s total. Both existing
  agent owners change 5 -> 2 at 2 s and 2 -> 5 at 5 s.

There are three new repetitions of each mode in each scenario: twelve runs.
Pair orders are CD, DC, CD for overload and DC, CD, DC for dynamic, balancing
which mode runs first over the whole series. Runs are sequential, each with
fresh private processes, identical warmup and the old 500 ms scheduling lead.
Servers use free loopback ports and temporary in-memory credentials. Only those
processes are stopped. No paid API or language model is involved.

## Additional measurements

All existing stage-4 raw and derived metrics remain: full logical-task records,
working HTTP attempts, end-to-end successful percentiles with outcome fractions,
429/queue/network/upstream errors, actual upstream starts/completions, atomic
admissions, sampled queues/executors/budgets and final drain checks.

Additional client events record discovery start and end separately from operation
attempts, policy application and final Gate state. A cancelled discovery remains
in the request count and is marked incomplete. Startup readiness probes and cleanup
policy checks performed by the harness are counted separately in cleanup metadata;
these lifecycle attempts are not reported as workload-client discovery.
Negative timestamps preserve initial discovery during the common scheduling lead.
No credentials or entire environment contents are logged.

Policy-application delay is matched by owner and **revision**, not only the
numeric limit (D retaining five is not an application of the later revision three).
Without changing the gateway, exact server mutation time cannot be observed.
The harness records admin request start and response completion; the mutation
lies inside that interval. For C, analysis reports delay bounds:

- lower: client application log time minus admin response completion, floored at zero;
- upper: client application log time minus admin request start, floored at zero.

The client logs after applying the policy, so this includes small logging and
cross-process clock alignment uncertainty. It is an observed interval, not a
precise server timestamp. D's unapplied revisions have null delays. Scheduled
versus actual admin timing is retained, and C must observe both dynamic revisions.

For dynamic runs, `summary.json` separates two views over nominal windows
initial [0,2s), reduced [2,5s), restored [5s,end):

1. **Arrival cohorts:** tasks grouped by planned arrival. Each keeps its full
   eventual outcome and end-to-end latency, even when it finishes after recovery.
2. **Activity in the window:** operation starts, 429 responses, discovery starts,
   actual upstream starts, and terminal outcomes timestamped inside that window.

Thus a task arriving under the reduced budget does not disappear or become a
new task when retried after recovery. Final failure reasons are retained per
class, per run and per arrival cohort. Preparation discovery is a separate
before_start window. Administrative boundaries can differ slightly from nominal
2/5 seconds; exact request/response times remain in the raw records.

Old accepted outstanding above a reduced limit is permitted. The analyzer reuses
the stage-4 atomic admission check, rather than treating every over-limit sample
as an error. C must finish with revision three/limit five in dynamic runs; D must
finish at initial revision one/limit five after exactly one valid policy per owner.

## Reproduction

From the repository root with its Python requirements installed:

```sh
cargo fmt --check
cargo check --locked --all-targets
cargo test --locked --all-targets
cargo build --locked --release --bins
python3 -m unittest discover -s tests -v
python3 benchmarks/stage4_1/run.py --output benchmarks/results/new-stage4_1
python3 benchmarks/stage4_1/comparison.py benchmarks/results/new-stage4_1
```

Use a new output directory. The runner imports the existing stage-4 harness and
upstream; only harness diagnostics add admin request-start and lifecycle discovery
counts. The server implementation is unchanged. Unit tests exercise C applying
5 -> 2 -> 5 while D stays fixed, revision-matched application bounds, and cohorts
that finish after recovery. Existing retry, Gate, server and measurement tests
are reused. The comparison checks real final Gate states and admissions as well.

Artifacts include configuration, exact schedules, acquisition environment/source/
binary hashes, JSONL task/HTTP/server/upstream/admin evidence, CSV budget series,
per-run summary and C-minus-D paired differences. The separate report is
[STAGE4_1_REPORT.md](../STAGE4_1_REPORT.md). Percentiles only describe successful
tasks, always accompanied by success/failure/unfinished fractions. Any aggregate
p95 is explicitly the median of per-run p95 values, never a pooled percentile.

## Limits

This is the same local, homogeneous delayed-I/O model as stage 4. Three paired
repetitions do not establish statistical significance or production CPU/database
performance. Different executions have runtime noise, despite identical scheduled
inputs. C's extra polling can have no payoff with constant policy. During changes
it may reduce refusals while adding waiting, or offer no meaningful benefit.
The experiment does not presuppose a winner and does not tune load for C.
