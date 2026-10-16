Results: 200 packets, N=4, k=3, timeout=0.9s (seed = 1051)

| Scenario | Protocol | Packets transmitted | Retransmissions | Delivered | Time (s) | Throughput (kbps) | Utilization (%) |
|---|---|---|---|---|---|---|---|
| A: Reliable | GBN | 200 | 0 | 200 | 25.21 | 63.5 | 3.17 |
| A: Reliable | SR | 200 | 0 | 200 | 25.21 | 63.5 | 3.17 |
| B: Loss 0.10 | GBN | 294 | 94 | 200 | 50.52 | 31.7 | 1.58 |
| B: Loss 0.10 | SR | 240 | 40 | 200 | 49.28 | 32.5 | 1.62 |
| C: Mixed (L0.05, C0.05, D0.10) | GBN | 353 | 153 | 200 | 72.21 | 22.2 | 1.11 |
| C: Mixed (L0.05, C0.05, D0.10) | SR | 260 | 60 | 200 | 61.81 | 25.9 | 1.29 |
