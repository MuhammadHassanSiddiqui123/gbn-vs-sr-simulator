#!/usr/bin/env python3
"""
Task 4: run GBN and SR (200 packets, N=4, k=3) under scenarios A, B, C and
produce results.csv, results_table.md and gbn_vs_sr.png.

Usage:
    python run_experiments.py --seed 1234            # your last 4 roll-number digits
    python run_experiments.py --seed 1234 --trials 30  # average over 30 seeds (extra evidence)
"""
import argparse
import csv
import statistics

from rdt import build_parser, run

SCENARIOS = [
    ("A: Reliable", dict(loss=0.0, corrupt=0.0, delay=0.0)),
    ("B: Loss 0.10", dict(loss=0.10, corrupt=0.0, delay=0.0)),
    ("C: Mixed (L0.05, C0.05, D0.10)", dict(loss=0.05, corrupt=0.05, delay=0.10)),
]
PROTOCOLS = ["gbn", "sr"]
METRICS = ["transmitted", "retransmissions", "delivered", "time", "throughput_kbps", "utilization"]


def one_run(proto, params, seed, timeout):
    argv = ["--protocol", proto, "--packets", "200", "--window", "4", "--bits", "3",
            "--seed", str(seed), "--quiet"]
    for k, v in params.items():
        argv += [f"--{k}", str(v)]
    if timeout is not None:
        argv += ["--timeout", str(timeout)]
    r = run(build_parser().parse_args(argv))
    if not r["correct"]:
        raise RuntimeError(f"{proto} failed under {params} seed={seed}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1234, help="last 4 digits of roll number")
    ap.add_argument("--trials", type=int, default=1, help="number of seeds to average over")
    ap.add_argument("--timeout", type=float, default=0.9,
                    help="same timeout for every run so scenarios are comparable")
    args = ap.parse_args()

    rows = []
    for name, params in SCENARIOS:
        for proto in PROTOCOLS:
            runs = [one_run(proto, params, args.seed + i, args.timeout) for i in range(args.trials)]
            row = dict(scenario=name, protocol=proto.upper())
            for m in METRICS:
                row[m] = statistics.mean(r[m] for r in runs)
            rows.append(row)

    with open("results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["scenario", "protocol"] + METRICS)
        w.writeheader()
        w.writerows(rows)

    note = (f"seed = {args.seed}" if args.trials == 1
            else f"mean of {args.trials} runs, seeds {args.seed}..{args.seed + args.trials - 1}")
    lines = [f"Results: 200 packets, N=4, k=3, timeout={args.timeout}s ({note})", "",
             "| Scenario | Protocol | Packets transmitted | Retransmissions | Delivered | "
             "Time (s) | Throughput (kbps) | Utilization (%) |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['scenario']} | {r['protocol']} | {r['transmitted']:.0f} | "
                     f"{r['retransmissions']:.0f} | {r['delivered']:.0f} | {r['time']:.2f} | "
                     f"{r['throughput_kbps']:.1f} | {r['utilization'] * 100:.2f} |")
    table = "\n".join(lines)
    open("results_table.md", "w").write(table + "\n")
    print(table)

    # ---- graph ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ["A: Reliable", "B: Loss 0.10", "C: Mixed"]
    x = range(len(labels))
    width = 0.38
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, metric, title, ylabel in [
        (axes[0], "retransmissions", "Retransmissions", "Retransmitted packets"),
        (axes[1], "time", "Completion time", "Time to deliver 200 packets (s)"),
    ]:
        for j, proto in enumerate(["GBN", "SR"]):
            vals = [r[metric] for r in rows if r["protocol"] == proto]
            bars = ax.bar([i + (j - 0.5) * width for i in x], vals, width,
                          label=proto, color=["#c0504d", "#4f81bd"][j])
            for b, v in zip(bars, vals):
                ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.0f}" if metric == "retransmissions"
                        else f"{v:.1f}", ha="center", va="bottom", fontsize=8)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels)
        ax.set_xlabel("Channel scenario")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend()
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle(f"GBN vs SR (200 packets, N=4, k=3) - {note}")
    fig.tight_layout()
    fig.savefig("gbn_vs_sr.png", dpi=150)
    print("\nSaved results.csv, results_table.md, gbn_vs_sr.png")


if __name__ == "__main__":
    main()
