# CS3001 Assignment-II: GBN and SR over a simulated unreliable satellite link

Python 3 only (standard library). `matplotlib` is needed just for the graph in `run_experiments.py`.

## Files
| File | Purpose |
|---|---|
| `rdt.py` | GBN + SR sender/receiver, checksum, simulated channel (loss / corruption / delay), logging |
| `run_experiments.py` | Runs scenarios A, B, C for both protocols; writes `results.csv`, `results_table.md`, `gbn_vs_sr.png` |

## How it works (one paragraph)
A discrete-event simulator keeps a clock and a queue of events (send, packet arrival, ACK arrival, timeout),
so runs are fast and reproducible for a given `--seed`. Every data packet and ACK goes through one channel
function that may lose it, corrupt it (one flipped bit, caught by a 16-bit Internet checksum) or delay it.
Link model: R = 2 Mbps, L = 1000 B (4 ms to transmit), RTT = 500 ms (250 ms each way).
Sequence numbers wrap modulo 2^k; all window/ACK decisions use modular arithmetic.

## Sample command
```
python rdt.py --protocol sr --packets 200 --window 4 --bits 3 --loss 0.05 --corrupt 0.05 --delay 0.10 --seed 1234
```
Reproduce the Task 2 trace (10 packets, packet 2 lost on first transmission):
```
python rdt.py --protocol gbn --packets 10 --window 4 --bits 3 --force-drop 2
python rdt.py --protocol sr  --packets 10 --window 4 --bits 3 --force-drop 2
```
Task 4 experiments (use the last 4 digits of YOUR roll number as the seed):
```
python run_experiments.py --seed 1234
python run_experiments.py --seed 1234 --trials 30     # optional: average over 30 seeds
```

## Parameters (`rdt.py`)
| Option | Default | Meaning |
|---|---|---|
| `--protocol {gbn,sr}` | required | protocol |
| `--packets` | 200 | number of data packets |
| `--window` | 4 | window size N |
| `--bits` | 3 | sequence-number bits k (numbers wrap mod 2^k) |
| `--loss` | 0 | loss probability (packets and ACKs) |
| `--corrupt` | 0 | corruption probability (packets and ACKs) |
| `--delay` | 0 | probability of extra delay (packets and ACKs) |
| `--delay-max` | 0.2 | maximum extra delay in seconds |
| `--timeout` | auto | retransmission timeout (s). Auto = RTT + 0.1 (no delay) or RTT + 2*delay-max + 0.1 |
| `--seed` | 1234 | random seed - **set to last 4 digits of your roll number** |
| `--size`, `--rate`, `--rtt` | 1000, 2e6, 0.5 | L (bytes), R (bit/s), RTT (s) |
| `--force-drop i j ...` | none | drop the first transmission of these packet indices (demos) |
| `--force` | off | allow window sizes above the safe maximum (to demonstrate ambiguity) |
| `--quiet` | off | summary only, no per-event log |

Safe-window checks: GBN needs N <= 2^k - 1; SR needs N <= 2^(k-1). Otherwise the program stops
unless `--force` is given.

## Log lines
`send`, `RETRANSMIT`, `*** LOSS`, `*** CORRUPT`, `*** DELAY`, `got ACKx`, `TIMEOUT`, receiver
`DELIVER`, `BUFFERED`, `OUT OF ORDER ... discarded`, window slides (`base`, `nextseqnum`, `rcv_base`).

## Output summary
Packets transmitted, retransmissions, delivered (verified in order and byte-for-byte correct),
timeouts, channel statistics, completion time, throughput (goodput) and link utilization.

## Assumptions
- Corrupted packets/ACKs are detected by the checksum and treated as discarded.
- A timeout must exceed the worst-case round trip. Otherwise a premature timeout creates duplicates and,
  with only 3-bit sequence numbers, a stale duplicate ACK can be mistaken for a new one (the program warns).
- The GBN timer restarts whenever `base` advances. The SR receiver re-ACKs packets from the previous window.
