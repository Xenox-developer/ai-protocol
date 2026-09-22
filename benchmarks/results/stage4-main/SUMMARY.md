# Automatically generated benchmark summary

Latency percentiles use successful logical tasks only, measured from scheduled arrival.
Every row is one run. No pooled percentile is inferred from run percentiles.

| Scenario | Rep | Mode | Class | Success/total | Failed | Unfinished | p50 ms | p95 ms | p99 ms | Attempts/task | 429 | Queue timeout | Network/upstream errors | Agent batch ms* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dynamic | 1 | A | interactive | 80/80 | 0 | 0 | 213.11 | 301.86 | 320.00 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 1 | A | agent | 330/360 | 30 | 0 | 1207.41 | 4216.75 | 4259.08 | 2.39 | 532 | 0 | 0/0 | n/a |
| dynamic | 1 | B | interactive | 80/80 | 0 | 0 | 212.58 | 235.01 | 242.46 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 1 | B | agent | 326/360 | 34 | 0 | 1207.38 | 4217.64 | 4305.88 | 2.38 | 532 | 0 | 0/0 | n/a |
| dynamic | 1 | C | interactive | 80/80 | 0 | 0 | 211.52 | 234.63 | 248.89 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 1 | C | agent | 360/360 | 0 | 0 | 1413.56 | 5472.61 | 7898.97 | 1.07 | 24 | 0 | 0/0 | 10490.19 |
| dynamic | 2 | A | interactive | 80/80 | 0 | 0 | 211.74 | 332.66 | 337.53 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 2 | A | agent | 323/360 | 37 | 0 | 1205.87 | 4218.21 | 4248.39 | 2.39 | 537 | 0 | 0/0 | n/a |
| dynamic | 2 | B | interactive | 80/80 | 0 | 0 | 211.05 | 249.29 | 260.78 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 2 | B | agent | 332/360 | 28 | 0 | 1206.36 | 4220.63 | 4305.67 | 2.38 | 524 | 0 | 0/0 | n/a |
| dynamic | 2 | C | interactive | 80/80 | 0 | 0 | 209.04 | 235.97 | 254.49 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 2 | C | agent | 360/360 | 0 | 0 | 1514.07 | 5145.67 | 7159.13 | 1.07 | 24 | 0 | 0/0 | 10568.30 |
| dynamic | 3 | A | interactive | 80/80 | 0 | 0 | 217.55 | 314.08 | 336.87 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 3 | A | agent | 326/360 | 34 | 0 | 1205.25 | 4226.10 | 4313.47 | 2.38 | 530 | 0 | 0/0 | n/a |
| dynamic | 3 | B | interactive | 80/80 | 0 | 0 | 211.81 | 232.12 | 242.18 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 3 | B | agent | 330/360 | 30 | 0 | 1204.55 | 4218.67 | 4225.85 | 2.36 | 519 | 0 | 0/0 | n/a |
| dynamic | 3 | C | interactive | 80/80 | 0 | 0 | 210.24 | 243.55 | 253.61 | 1.00 | 0 | 0 | 0/0 | n/a |
| dynamic | 3 | C | agent | 360/360 | 0 | 0 | 1352.53 | 5366.24 | 6711.30 | 1.07 | 25 | 0 | 0/0 | 10545.38 |
| interactive_only | 1 | A | interactive | 90/90 | 0 | 0 | 205.89 | 211.20 | 222.85 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 1 | A | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| interactive_only | 1 | B | interactive | 90/90 | 0 | 0 | 207.24 | 210.91 | 213.25 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 1 | B | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| interactive_only | 1 | C | interactive | 90/90 | 0 | 0 | 206.89 | 214.65 | 218.06 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 1 | C | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| interactive_only | 2 | A | interactive | 90/90 | 0 | 0 | 206.51 | 214.66 | 216.35 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 2 | A | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| interactive_only | 2 | B | interactive | 90/90 | 0 | 0 | 205.29 | 209.90 | 216.29 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 2 | B | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| interactive_only | 2 | C | interactive | 90/90 | 0 | 0 | 206.44 | 210.71 | 213.25 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 2 | C | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| interactive_only | 3 | A | interactive | 90/90 | 0 | 0 | 206.75 | 223.72 | 239.79 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 3 | A | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| interactive_only | 3 | B | interactive | 90/90 | 0 | 0 | 205.69 | 211.25 | 220.81 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 3 | B | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| interactive_only | 3 | C | interactive | 90/90 | 0 | 0 | 207.13 | 215.54 | 232.48 | 1.00 | 0 | 0 | 0/0 | n/a |
| interactive_only | 3 | C | agent | 0/0 | 0 | 0 | n/a | n/a | n/a | n/a | 0 | 0 | 0/0 | n/a |
| mixed_low | 1 | A | interactive | 48/48 | 0 | 0 | 207.55 | 211.43 | 219.28 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 1 | A | agent | 120/120 | 0 | 0 | 207.16 | 213.04 | 231.83 | 1.00 | 0 | 0 | 0/0 | 6159.11 |
| mixed_low | 1 | B | interactive | 48/48 | 0 | 0 | 207.53 | 212.51 | 214.62 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 1 | B | agent | 120/120 | 0 | 0 | 206.90 | 214.86 | 217.65 | 1.00 | 0 | 0 | 0/0 | 6166.99 |
| mixed_low | 1 | C | interactive | 48/48 | 0 | 0 | 207.38 | 210.78 | 221.92 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 1 | C | agent | 120/120 | 0 | 0 | 205.52 | 211.94 | 212.80 | 1.00 | 0 | 0 | 0/0 | 6155.40 |
| mixed_low | 2 | A | interactive | 48/48 | 0 | 0 | 206.66 | 210.49 | 215.08 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 2 | A | agent | 120/120 | 0 | 0 | 206.13 | 210.96 | 215.00 | 1.00 | 0 | 0 | 0/0 | 6165.50 |
| mixed_low | 2 | B | interactive | 48/48 | 0 | 0 | 206.60 | 212.93 | 215.29 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 2 | B | agent | 120/120 | 0 | 0 | 205.77 | 212.92 | 218.36 | 1.00 | 0 | 0 | 0/0 | 6171.26 |
| mixed_low | 2 | C | interactive | 48/48 | 0 | 0 | 206.86 | 212.01 | 215.14 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 2 | C | agent | 120/120 | 0 | 0 | 206.51 | 212.00 | 216.80 | 1.00 | 0 | 0 | 0/0 | 6158.71 |
| mixed_low | 3 | A | interactive | 48/48 | 0 | 0 | 205.82 | 211.41 | 235.26 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 3 | A | agent | 120/120 | 0 | 0 | 205.50 | 215.85 | 235.60 | 1.00 | 0 | 0 | 0/0 | 6161.11 |
| mixed_low | 3 | B | interactive | 48/48 | 0 | 0 | 207.05 | 215.28 | 280.66 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 3 | B | agent | 120/120 | 0 | 0 | 207.14 | 224.96 | 273.15 | 1.00 | 0 | 0 | 0/0 | 6154.52 |
| mixed_low | 3 | C | interactive | 48/48 | 0 | 0 | 207.28 | 216.33 | 225.05 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_low | 3 | C | agent | 120/120 | 0 | 0 | 207.34 | 216.63 | 220.20 | 1.00 | 0 | 0 | 0/0 | 6157.48 |
| mixed_overload | 1 | A | interactive | 90/90 | 0 | 0 | 286.97 | 346.85 | 362.51 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 1 | A | agent | 351/480 | 129 | 0 | 1233.08 | 4229.02 | 4369.86 | 3.16 | 1166 | 0 | 0/0 | n/a |
| mixed_overload | 1 | B | interactive | 90/90 | 0 | 0 | 232.66 | 310.04 | 323.56 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 1 | B | agent | 353/480 | 127 | 0 | 1259.83 | 4223.69 | 4342.07 | 3.18 | 1174 | 0 | 0/0 | n/a |
| mixed_overload | 1 | C | interactive | 90/90 | 0 | 0 | 238.58 | 343.42 | 352.59 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 1 | C | agent | 477/480 | 3 | 0 | 2949.15 | 8306.78 | 9702.56 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 2 | A | interactive | 90/90 | 0 | 0 | 269.72 | 335.97 | 345.22 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 2 | A | agent | 345/480 | 135 | 0 | 1210.43 | 4221.59 | 4331.03 | 3.12 | 1151 | 0 | 0/0 | n/a |
| mixed_overload | 2 | B | interactive | 90/90 | 0 | 0 | 236.99 | 315.66 | 331.20 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 2 | B | agent | 343/480 | 137 | 0 | 1253.38 | 4226.64 | 4323.38 | 3.22 | 1202 | 0 | 0/0 | n/a |
| mixed_overload | 2 | C | interactive | 90/90 | 0 | 0 | 242.83 | 322.94 | 329.93 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 2 | C | agent | 479/480 | 1 | 0 | 2870.23 | 8362.86 | 9596.60 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 3 | A | interactive | 90/90 | 0 | 0 | 267.61 | 337.55 | 352.32 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 3 | A | agent | 347/480 | 133 | 0 | 1205.78 | 4223.85 | 4349.74 | 3.13 | 1157 | 0 | 0/0 | n/a |
| mixed_overload | 3 | B | interactive | 90/90 | 0 | 0 | 229.36 | 311.45 | 320.37 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 3 | B | agent | 347/480 | 133 | 0 | 365.58 | 4223.12 | 4351.00 | 3.11 | 1144 | 0 | 0/0 | n/a |
| mixed_overload | 3 | C | interactive | 90/90 | 0 | 0 | 233.88 | 319.04 | 332.30 | 1.00 | 0 | 0 | 0/0 | n/a |
| mixed_overload | 3 | C | agent | 472/480 | 8 | 0 | 2912.58 | 7839.73 | 9266.80 | 1.02 | 14 | 0 | 0/0 | n/a |

*Batch time is present only when every agent task succeeded; n/a is not a fast completion.

## Separate A/B and B/C comparisons

Values below are medians across runs, including explicitly the median of per-run p95 values.

| Scenario | Comparison | Class | Median run p95: left -> right (ms) | Median success fraction: left -> right | Median 429: left -> right |
|---|---|---|---|---|---|
| dynamic | A -> B | interactive | 314.08 -> 235.01 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| dynamic | A -> B | agent | 4218.21 -> 4218.67 | 0.91 -> 0.92 | 532.00 -> 524.00 |
| dynamic | B -> C | interactive | 235.01 -> 235.97 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| dynamic | B -> C | agent | 4218.67 -> 5366.24 | 0.92 -> 1.00 | 524.00 -> 24.00 |
| interactive_only | A -> B | interactive | 214.66 -> 210.91 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| interactive_only | A -> B | agent | n/a -> n/a | n/a -> n/a | 0.00 -> 0.00 |
| interactive_only | B -> C | interactive | 210.91 -> 214.65 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| interactive_only | B -> C | agent | n/a -> n/a | n/a -> n/a | 0.00 -> 0.00 |
| mixed_low | A -> B | interactive | 211.41 -> 212.93 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| mixed_low | A -> B | agent | 213.04 -> 214.86 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| mixed_low | B -> C | interactive | 212.93 -> 212.01 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| mixed_low | B -> C | agent | 214.86 -> 212.00 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| mixed_overload | A -> B | interactive | 337.55 -> 311.45 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| mixed_overload | A -> B | agent | 4223.85 -> 4223.69 | 0.72 -> 0.72 | 1157.00 -> 1174.00 |
| mixed_overload | B -> C | interactive | 311.45 -> 322.94 | 1.00 -> 1.00 | 0.00 -> 0.00 |
| mixed_overload | B -> C | agent | 4223.69 -> 8306.78 | 0.72 -> 0.99 | 1174.00 -> 0.00 |

See summary.json for exact fractions, actual upstream counts, generator lag, and admission checks.
See each run directory for raw events, reconstructed tasks, and queue/executor/budget timeseries.
Incomplete final diagnostic samples: 1. Raw files are preserved; interior corruption is fatal.
