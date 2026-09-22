# Automatically generated benchmark summary

Latency percentiles use successful logical tasks only, measured from scheduled arrival.
Every row is one run. No pooled percentile is inferred from run percentiles.

| Scenario | Rep | Mode | Class | Success/total | Failed | Unfinished | p50 ms | p95 ms | p99 ms | Attempts/task | 429 | Queue timeout | Network/upstream errors | Agent batch ms* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pilot | 1 | A | interactive | 80/80 | 0 | 0 | 7.18 | 11.92 | 16.23 | 1.00 | 0 | 0 | 0/0 | n/a |
| pilot | 1 | A | agent | 160/160 | 0 | 0 | 6.55 | 11.78 | 16.31 | 1.00 | 0 | 0 | 0/0 | 1994.72 |
| pilot | 1 | B | interactive | 80/80 | 0 | 0 | 5.37 | 13.65 | 34.34 | 1.00 | 0 | 0 | 0/0 | n/a |
| pilot | 1 | B | agent | 160/160 | 0 | 0 | 5.37 | 14.06 | 34.68 | 1.00 | 0 | 0 | 0/0 | 1993.73 |
| pilot | 1 | C | interactive | 80/80 | 0 | 0 | 6.41 | 27.45 | 58.11 | 1.00 | 0 | 0 | 0/0 | n/a |
| pilot | 1 | C | agent | 160/160 | 0 | 0 | 5.51 | 26.57 | 52.93 | 1.00 | 0 | 0 | 0/0 | 1991.70 |

*Batch time is present only when every agent task succeeded; n/a is not a fast completion.

## Separate A/B and B/C comparisons

Values below are medians across runs, including explicitly the median of per-run p95 values.

| Scenario | Comparison | Class | Median run p95: left -> right (ms) | Median success fraction: left -> right | Median 429: left -> right |
|---|---|---|---|---|---|
| pilot | A -> B | interactive | 11.92 -> 13.65 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| pilot | A -> B | agent | 11.78 -> 14.06 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| pilot | B -> C | interactive | 13.65 -> 27.45 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| pilot | B -> C | agent | 14.06 -> 26.57 | 1.00 -> 1.00 | 0.00 -> 0.00 |

See summary.json for exact fractions, actual upstream counts, generator lag, and admission checks.
See each run directory for raw events, reconstructed tasks, and queue/executor/budget timeseries.
Incomplete final diagnostic samples: 0. Raw files are preserved; interior corruption is fatal.
