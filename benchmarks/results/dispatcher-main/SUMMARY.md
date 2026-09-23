# Four agent processes: independent C versus shared dispatcher S

Latency includes all waiting from planned arrival; percentiles cover successful tasks only.
Discovery includes dispatcher identity verification; harness probes are separate.

| Scenario | Rep | Mode | Class | Success/total | p50 / p95 / p99 ms | Working requests | 429 | Discovery* |
|---|---|---|---|---|---|---|---|---|
| dynamic | 1 | C | agent | 174/180 | 1207.5 / 4222.4 / 4226.6 | 376 | 202 | 43 |
| dynamic | 1 | C | interactive | 80/80 | 206.8 / 210.7 / 215.4 | 80 | 0 | 43 |
| dynamic | 1 | S | agent | 180/180 | 1974.2 / 2193.6 / 2916.2 | 187 | 7 | 15 |
| dynamic | 1 | S | interactive | 80/80 | 206.8 / 217.2 / 238.9 | 80 | 0 | 15 |
| dynamic | 2 | C | agent | 172/180 | 490.8 / 3338.8 / 4301.9 | 377 | 205 | 44 |
| dynamic | 2 | C | interactive | 80/80 | 205.4 / 219.3 / 289.0 | 80 | 0 | 44 |
| dynamic | 2 | S | agent | 180/180 | 1926.2 / 2203.9 / 3039.7 | 188 | 8 | 15 |
| dynamic | 2 | S | interactive | 80/80 | 206.7 / 215.2 / 230.9 | 80 | 0 | 15 |
| dynamic | 3 | C | agent | 170/180 | 299.5 / 3414.8 / 4272.0 | 383 | 213 | 47 |
| dynamic | 3 | C | interactive | 80/80 | 206.8 / 242.5 / 313.6 | 80 | 0 | 47 |
| dynamic | 3 | S | agent | 180/180 | 1918.4 / 2192.2 / 2932.9 | 187 | 7 | 15 |
| dynamic | 3 | S | interactive | 80/80 | 205.9 / 214.6 / 220.3 | 80 | 0 | 15 |
| mixed_overload | 1 | C | agent | 207/240 | 1209.4 / 4225.5 / 4230.2 | 653 | 446 | 40 |
| mixed_overload | 1 | C | interactive | 90/90 | 206.6 / 213.4 / 230.0 | 90 | 0 | 40 |
| mixed_overload | 1 | S | agent | 240/240 | 2060.0 / 3807.2 / 3966.0 | 240 | 0 | 15 |
| mixed_overload | 1 | S | interactive | 90/90 | 206.9 / 214.6 / 222.3 | 90 | 0 | 15 |
| mixed_overload | 2 | C | agent | 207/240 | 1213.8 / 4225.8 / 4236.8 | 663 | 456 | 41 |
| mixed_overload | 2 | C | interactive | 90/90 | 206.9 / 223.9 / 231.0 | 90 | 0 | 41 |
| mixed_overload | 2 | S | agent | 240/240 | 2070.0 / 3836.0 / 4001.2 | 240 | 0 | 15 |
| mixed_overload | 2 | S | interactive | 90/90 | 206.9 / 214.7 / 219.6 | 90 | 0 | 15 |
| mixed_overload | 3 | C | agent | 205/240 | 1208.9 / 4224.2 / 4231.1 | 658 | 453 | 41 |
| mixed_overload | 3 | C | interactive | 90/90 | 206.8 / 217.0 / 241.7 | 90 | 0 | 41 |
| mixed_overload | 3 | S | agent | 240/240 | 2061.1 / 3828.6 / 3990.2 | 240 | 0 | 15 |
| mixed_overload | 3 | S | interactive | 90/90 | 206.9 / 213.3 / 218.1 | 90 | 0 | 15 |

*Run-wide discovery repeated across class rows; do not sum the two rows.
