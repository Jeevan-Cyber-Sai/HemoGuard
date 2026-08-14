"""Derive scoring baselines from a recorded run.

The bleeding-rate, pulse and SpO2 baselines are anchored to published clinical
figures and ship with sensible defaults. The OPTICAL one cannot be: the
haemoglobin index is an absorbance, and its absolute scale depends on the path
length, the cuvette, the LED brightness and the sensor gain of the individual
rig. A number that means "heavy bleeding" on one setup means "barely stained"
on another.

So measure it. Record a run with water on the sensor, then a run with the blood
sample, and this reads the CSV and reports the baseline that puts your sample at
whatever z-score you say it deserves.

    python tools/tune_baselines.py logs/experiment_log.csv ^
        --water 14:05:00-14:06:00 ^
        --sample 14:08:00-14:09:00

Times are HH:MM:SS, matched against the timestamp column. Add --z to choose the
z-score the sample should land on (default 2.5, the red threshold).
"""

import argparse
import csv
import statistics
import sys
from datetime import datetime


def parse_window(text):
    try:
        start, end = text.split("-")
        return start.strip(), end.strip()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected HH:MM:SS-HH:MM:SS, got {text!r}")


def in_window(stamp, window):
    """stamp is an ISO timestamp; window a (HH:MM:SS, HH:MM:SS) pair."""
    try:
        clock = datetime.fromisoformat(stamp).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return False
    return window[0] <= clock <= window[1]


def collect(path, column, window):
    values = []
    modes = set()
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not in_window(row.get("timestamp", ""), window):
                continue
            raw = row.get(column, "")
            if raw in ("", None):
                continue
            try:
                values.append(float(raw))
            except ValueError:
                continue
            if row.get("hb_mode"):
                modes.add(row["hb_mode"])
    return values, modes


def describe(label, values):
    if not values:
        print(f"  {label}: no rows matched that window")
        return None
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    print(f"  {label}: n={len(values)}  mean={mean:.4f}  sd={sd:.4f}  "
          f"min={min(values):.4f}  max={max(values):.4f}")
    return mean


def main():
    parser = argparse.ArgumentParser(
        description="Derive HEMOGUARD_BASELINE_HB from a recorded run")
    parser.add_argument("csv", help="path to experiment_log.csv")
    parser.add_argument("--water", type=parse_window, required=True,
                        metavar="HH:MM:SS-HH:MM:SS",
                        help="window where water was on the sensor")
    parser.add_argument("--sample", type=parse_window, required=True,
                        metavar="HH:MM:SS-HH:MM:SS",
                        help="window where the blood sample was on the sensor")
    parser.add_argument("--z", type=float, default=2.5,
                        help="z-score the sample should land on (default 2.5)")
    parser.add_argument("--column", default="hb_index")
    args = parser.parse_args()

    if not args.z > 0:
        sys.exit("--z must be greater than 0")

    print(f"Reading {args.csv}, column {args.column!r}\n")

    water, water_modes = collect(args.csv, args.column, args.water)
    sample, sample_modes = collect(args.csv, args.column, args.sample)

    water_mean = describe("water ", water)
    sample_mean = describe("sample", sample)
    print()

    if water_mean is None or sample_mean is None:
        sys.exit("Need rows in both windows. Check the times against the CSV.")

    modes = water_modes | sample_modes
    if modes and modes != {"absorbance"}:
        print(f"  WARNING: hb_mode in this data is {sorted(modes)}.")
        print("  Only 'absorbance' rows are meaningful - a chromaticity has no")
        print("  baseline it can be scaled against. Re-record after calibrating")
        print("  the node against water.\n")

    if sample_mean <= water_mean:
        print("  The sample did not absorb more than the water reference.")
        print("  Either the baseline was taken after the sample was in place,")
        print("  or the cuvette moved between the two runs. Re-calibrate and")
        print("  record again - nothing useful can be derived from this.")
        sys.exit(1)

    # Water defines the zero, so the mean stays there and the sd is chosen to
    # put the sample at the requested z.
    mean = round(water_mean, 4)
    sd = round((sample_mean - water_mean) / args.z, 4)

    print(f"  Sample sits {sample_mean - water_mean:.4f} above water.")
    print(f"  For that to read z={args.z:g}:\n")
    print(f"    set HEMOGUARD_BASELINE_HB={mean},{sd}\n")
    print("  Then restart uvicorn. Check the result with a fresh run:")
    print(f"    water should score z_hb near 0, the sample near {args.z:g}.")

    for z_label, z_value in (("amber", 1.0), ("red", 2.5)):
        print(f"    {z_label:5} at hb_index >= {mean + z_value * sd:.4f}")


if __name__ == "__main__":
    main()
