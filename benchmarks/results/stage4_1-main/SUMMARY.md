# Stage 4.1 per-run summary

Percentiles cover successful logical tasks only, from scheduled arrival. Each row is one run.
Discovery counts are client policy requests; harness probes are separate in JSON.

| Scenario | Rep | Mode | Class | Success / total | Failed | Unfinished | p50 / p95 / p99 ms | Attempts/task | 429 | Discovery* | Actual upstream* |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dynamic | 1 | C | interactive | 80/80 | 0 | 0 | 208.49 / 234.25 / 248.60 | 1.000 | 0 | 22 | 440 |
| dynamic | 1 | C | agent | 360/360 | 0 | 0 | 1246.47 / 5068.01 / 7137.18 | 1.067 | 24 | 22 | 440 |
| dynamic | 1 | D | interactive | 80/80 | 0 | 0 | 208.38 / 235.79 / 249.15 | 1.000 | 0 | 2 | 440 |
| dynamic | 1 | D | agent | 360/360 | 0 | 0 | 1206.87 / 5312.46 / 7284.27 | 1.536 | 193 | 2 | 440 |
| dynamic | 2 | C | interactive | 80/80 | 0 | 0 | 211.04 / 228.85 / 243.84 | 1.000 | 0 | 22 | 440 |
| dynamic | 2 | C | agent | 360/360 | 0 | 0 | 1328.44 / 5680.66 / 7020.25 | 1.067 | 24 | 22 | 440 |
| dynamic | 2 | D | interactive | 80/80 | 0 | 0 | 215.49 / 254.60 / 263.58 | 1.000 | 0 | 2 | 440 |
| dynamic | 2 | D | agent | 360/360 | 0 | 0 | 1219.29 / 5315.38 / 6316.41 | 1.536 | 193 | 2 | 440 |
| dynamic | 3 | C | interactive | 80/80 | 0 | 0 | 208.64 / 226.27 / 232.29 | 1.000 | 0 | 22 | 440 |
| dynamic | 3 | C | agent | 360/360 | 0 | 0 | 1287.30 / 5674.44 / 7549.89 | 1.069 | 25 | 22 | 440 |
| dynamic | 3 | D | interactive | 80/80 | 0 | 0 | 209.62 / 243.84 / 250.60 | 1.000 | 0 | 2 | 440 |
| dynamic | 3 | D | agent | 360/360 | 0 | 0 | 1209.51 / 5603.22 / 7516.88 | 1.536 | 193 | 2 | 440 |
| mixed_overload | 1 | C | interactive | 90/90 | 0 | 0 | 237.82 / 332.12 / 342.30 | 1.000 | 0 | 28 | 564 |
| mixed_overload | 1 | C | agent | 472/480 | 8 | 0 | 2938.46 / 8288.97 / 9313.52 | 1.044 | 27 | 28 | 564 |
| mixed_overload | 1 | D | interactive | 90/90 | 0 | 0 | 231.15 / 324.88 / 340.62 | 1.000 | 0 | 2 | 568 |
| mixed_overload | 1 | D | agent | 478/480 | 2 | 0 | 2995.16 / 8045.15 / 9509.96 | 0.996 | 0 | 2 | 568 |
| mixed_overload | 2 | C | interactive | 90/90 | 0 | 0 | 236.37 / 302.77 / 318.50 | 1.000 | 0 | 26 | 561 |
| mixed_overload | 2 | C | agent | 469/480 | 11 | 0 | 2890.72 / 8298.25 / 9502.99 | 1.085 | 50 | 26 | 561 |
| mixed_overload | 2 | D | interactive | 90/90 | 0 | 0 | 228.84 / 335.45 / 344.63 | 1.000 | 0 | 2 | 565 |
| mixed_overload | 2 | D | agent | 474/480 | 6 | 0 | 2984.28 / 7762.17 / 8934.96 | 1.044 | 26 | 2 | 565 |
| mixed_overload | 3 | C | interactive | 90/90 | 0 | 0 | 226.78 / 298.94 / 311.04 | 1.000 | 0 | 30 | 562 |
| mixed_overload | 3 | C | agent | 469/480 | 11 | 0 | 2839.43 / 7950.21 / 9585.56 | 1.052 | 33 | 30 | 562 |
| mixed_overload | 3 | D | interactive | 90/90 | 0 | 0 | 227.12 / 309.15 / 327.83 | 1.000 | 0 | 2 | 566 |
| mixed_overload | 3 | D | agent | 475/480 | 5 | 0 | 2955.77 / 7989.95 / 9457.39 | 0.996 | 2 | 2 | 566 |

*Run-wide values repeated across class rows; do not sum these two rows.

## Policy application delay

Mutation occurs inside the recorded admin request/response interval. Bounds below use the client application log timestamp.

| Rep | Mode | Owner | Limit | Delay lower..upper (ms) |
|---|---|---|---|---|
| 1 | C | demo-owner | 2 | 525.79..528.56 |
| 1 | C | other-owner | 2 | 524.48..528.11 |
| 1 | C | demo-owner | 5 | 541.19..542.90 |
| 1 | C | other-owner | 5 | 540.33..542.13 |
| 1 | D | demo-owner | 2 | not applied (fixed initial policy) |
| 1 | D | other-owner | 2 | not applied (fixed initial policy) |
| 1 | D | demo-owner | 5 | not applied (fixed initial policy) |
| 1 | D | other-owner | 5 | not applied (fixed initial policy) |
| 2 | C | demo-owner | 2 | 518.64..524.51 |
| 2 | C | other-owner | 2 | 517.42..521.05 |
| 2 | C | demo-owner | 5 | 527.93..530.99 |
| 2 | C | other-owner | 5 | 526.45..529.02 |
| 2 | D | demo-owner | 2 | not applied (fixed initial policy) |
| 2 | D | other-owner | 2 | not applied (fixed initial policy) |
| 2 | D | demo-owner | 5 | not applied (fixed initial policy) |
| 2 | D | other-owner | 5 | not applied (fixed initial policy) |
| 3 | C | demo-owner | 2 | 517.51..524.88 |
| 3 | C | other-owner | 2 | 515.71..519.70 |
| 3 | C | demo-owner | 5 | 533.78..535.74 |
| 3 | C | other-owner | 5 | 534.11..536.01 |
| 3 | D | demo-owner | 2 | not applied (fixed initial policy) |
| 3 | D | other-owner | 2 | not applied (fixed initial policy) |
| 3 | D | demo-owner | 5 | not applied (fixed initial policy) |
| 3 | D | other-owner | 5 | not applied (fixed initial policy) |

## Phase interpretation

summary.json includes both full outcomes for arrival cohorts and events occurring in each time window.
Dynamic windows: initial [0,2s), reduced [2,5s), restored [5s,end). Actual admin timing is retained separately.
A reduced-window arrival can finish after recovery; its full latency and outcome stay in the reduced arrival cohort.
Terminal failure reasons, queue/budget snapshots, discovery failures and interrupted requests remain in raw/derived JSON.
Incomplete final diagnostic samples: 0.
