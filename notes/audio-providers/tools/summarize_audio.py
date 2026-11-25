"""Turn results/audio_<provider>_<run>.json into median tables.

Writes results/audio_summary.csv (provider, format, metric, median, min,
max, n) and prints markdown tables for results.md.

With ``--ios results/bench_ios.json`` it summarizes the single on-device run
instead.
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
RES = HERE / "results"

FORMATS = ["wav", "aiff", "flac", "mp3", "ogg", "opus", "m4a", "aac", "caf"]
TABLE_METRICS = [
    ("plays", "plays (tone10)"),
    ("short_plays", "plays (short1)"),
    ("length_err_ms", "length error (ms)"),
    ("load_ms", "load (ms)"),
    ("play_call_ms", "play() call duration (ms)"),
    ("play_dispatches_on_stop", "play() dispatches on_stop"),
    ("start_latency_ms", "start latency (ms)"),
    ("drift_ms", "get_pos drift over 6 s (ms)"),
    ("pos_jitter_ms", "get_pos jitter (ms)"),
    ("seek7_0_immediate_err_ms", "seek 7.0 immediate err (ms)"),
    ("seek7_0_after200ms_err_ms", "seek 7.0 err after 200 ms (ms)"),
    ("on_stop_late_from_start_ms", "on_stop late vs audio end (ms)"),
    ("loop_wraps_in_3_6s", "loop wraps in 3.6 s"),
    ("loop_overhead_ms", "loop overhead per pass (ms)"),
    ("loop_max_stall_ms", "loop max stall (ms)"),
    ("loop_pos_glitches", "loop pos glitches"),
    ("loop_on_stop_events", "on_stop events while looping 3.6 s"),
    ("loop_on_play_events", "on_play events while looping 3.6 s"),
    ("pitch1_5_pos_rate", "pos rate at pitch=1.5"),
]


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.2f}" if abs(v) < 10 else f"{v:.0f}"
    return str(v)


def collect(runs_by_provider):
    """{provider: {format: {metric: [values]}}}"""
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for provider, runs in runs_by_provider.items():
        for rows in runs:
            for row in rows:
                for k, v in row.items():
                    if k == "format":
                        continue
                    data[provider][row["format"]][k].append(v)
    return data


def median(vals):
    nums = [v for v in vals if isinstance(v, (int, float))
            and not isinstance(v, bool)]
    if nums:
        return statistics.median(nums)
    return vals[0] if vals else None


def print_tables(data, providers):
    print("### Format coverage\n")
    print("| format | " + " | ".join(
        f"{p} declared | {p} opens | {p} plays 10 s / 1 s"
        for p in providers) + " |")
    print("|---" * (1 + 3 * len(providers)) + "|")
    for ext in FORMATS:
        cells = []
        for p in providers:
            d = data[p][ext]
            cells += [fmt(median(d.get("declared", [None]))),
                      fmt(median(d.get("loaded", [None]))),
                      fmt(median(d.get("plays", [None]))) + " / "
                      + fmt(median(d.get("short_plays", [None])))]
        print(f"| {ext} | " + " | ".join(cells) + " |")
    print()
    for key, label in TABLE_METRICS:
        print(f"### {label}\n")
        print("| provider | " + " | ".join(FORMATS) + " |")
        print("|---" * (1 + len(FORMATS)) + "|")
        for p in providers:
            cells = [fmt(median(data[p][ext].get(key, [None])))
                     for ext in FORMATS]
            print(f"| {p} | " + " | ".join(cells) + " |")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ios")
    args = ap.parse_args()

    if args.ios:
        result = json.loads(Path(args.ios).read_text())
        runs = {p: [rows] for p, rows in result["providers"].items()}
        data = collect(runs)
        for k, v in result.items():
            if k != "providers":
                print(f"- {k}: {fmt(v)}")
        print()
        print_tables(data, list(runs))
        return

    runs = defaultdict(list)
    stress = defaultdict(lambda: defaultdict(list))
    for path in sorted(RES.glob("audio_*_*.json")):
        r = json.loads(path.read_text())
        runs[r["provider"]].append(r["formats"])
        for k, v in r["stress"].items():
            stress[r["provider"]][k].append(v)
    data = collect(runs)
    providers = sorted(runs)

    with open(RES / "audio_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["provider", "format", "metric", "median", "min", "max",
                    "n"])
        for p in providers:
            for ext in FORMATS:
                for k, vals in sorted(data[p][ext].items()):
                    nums = [v for v in vals if isinstance(v, (int, float))
                            and not isinstance(v, bool)]
                    if nums:
                        w.writerow([p, ext, k, statistics.median(nums),
                                    min(nums), max(nums), len(nums)])
                    else:
                        w.writerow([p, ext, k, vals[0], "", "", len(vals)])
            for k, vals in sorted(stress[p].items()):
                w.writerow([p, "stress", k, median(vals),
                            min(vals), max(vals), len(vals)])

    print("runs: " + ", ".join(f"{p}={len(runs[p])}" for p in providers))
    print()
    print_tables(data, providers)
    print("### Stress (median of runs)\n")
    keys = sorted({k for p in providers for k in stress[p]})
    print("| metric | " + " | ".join(providers) + " |")
    print("|---" * (1 + len(providers)) + "|")
    for k in keys:
        print(f"| {k} | " + " | ".join(
            fmt(median(stress[p][k])) for p in providers) + " |")


if __name__ == "__main__":
    main()
