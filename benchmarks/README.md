# Load tests

Both generators first send human traffic at 5 requests/s
for 10 seconds, then send human traffic at 5 requests/s
and agent traffic at 120 requests/s concurrently for another 10 seconds.
They wait for all tasks to finish and report successful requests, errors,
and the p95 latency of successful tasks.

First, start the catalog and server using the [setup instructions](../README.md#getting-started).
Run the generators one at a time from the repository root:

```sh
cargo run --locked --release --bin load
cargo run --locked --release --bin load_plain
```

- `load` fetches the v2 policy, limits agent concurrency according to
  `max_in_flight`, and makes at most five attempts on `429`, respecting `Retry-After`.
- `load_plain` sends requests without a client-side concurrency limiter or retries.

Both clients send `POST /search` with `{"query":""}`. Task duration
includes client-side waiting and retries, but excludes delays in starting
relative to the schedule. The p95 value covers only successful tasks:
compare it alongside the error count.

## Saved results

- `results/shared/run-1.txt` … `run-5.txt` — the previous setup with a shared queue.
- `results/priority/run-1.txt` … `run-5.txt` — the previous setup with human priority.

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
