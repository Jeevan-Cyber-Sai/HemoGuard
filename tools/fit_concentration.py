"""Fit haemoglobin index -> real concentration (g/dL) from known samples.

Beer-Lambert says A = eps * l * c, so absorbance is LINEAR in concentration.
That is the property this exploits, and the property it checks: measure several
samples of known concentration, regress index against concentration, and keep
the fit. If the fit is not linear, the assumption behind the whole measurement
has broken and the R^2 will say so.

Neither eps nor l can be known on this rig - the LED's peak wavelength is
unspecified, the TCS34725's filters are broad rather than narrow, and the path
length depends on the sample holder - so this regression IS the calibration.

Procedure:
  1. Calibrate the node against water (the dashboard's CALIBRATE button).
  2. Prepare a dilution series from a stock of KNOWN concentration. If the stock
     was measured by a lab analyser, use that figure; halving the stock halves
     the concentration.
  3. Run each dilution for ~30 s, noting the clock times. Do not move the
     cuvette between them - a change in path length is indistinguishable from a
     change in concentration.
  4. Feed the windows and their concentrations to this script.

    python tools/fit_concentration.py logs/experiment_log.csv ^
        --point 14:05:00-14:06:00=0 ^
        --point 14:08:00-14:09:00=3.5 ^
        --point 14:11:00-14:12:00=7.0 ^
        --point 14:14:00-14:15:00=14.0
"""

import argparse
import csv
import statistics
import sys
from datetime import datetime

# Beer-Lambert stops being linear once almost no light gets through: the
# detector is then reading its own noise floor rather than the sample.
ABSORBANCE_LINEAR_CEILING = 1.5

# Below this the fit is not describing a straight line, and a concentration read
# off it would be a guess dressed up as a measurement.
MIN_ACCEPTABLE_R2 = 0.99


def parse_point(text):
    """'HH:MM:SS-HH:MM:SS=12.5' -> ((start, end), 12.5)"""
    try:
        window, concentration = text.rsplit("=", 1)
        start, end = window.split("-")
        return (start.strip(), end.strip()), float(concentration)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected HH:MM:SS-HH:MM:SS=CONC, got {text!r}")


def in_window(stamp, window):
    try:
        clock = datetime.fromisoformat(stamp).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return False
    return window[0] <= clock <= window[1]


def collect(path, column, window):
    values, modes = [], set()
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


def linear_fit(xs, ys):
    """Least-squares y = slope*x + intercept, plus R^2."""
    n = len(xs)
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("all samples produced the same index - "
                         "nothing to regress against")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot else 1.0
    return slope, intercept, r2


def main():
    parser = argparse.ArgumentParser(
        description="Fit hb_index -> g/dL from a dilution series")
    parser.add_argument("csv", help="path to experiment_log.csv")
    parser.add_argument("--point", type=parse_point, action="append",
                        required=True, metavar="HH:MM:SS-HH:MM:SS=CONC",
                        help="time window and its known concentration in g/dL")
    parser.add_argument("--column", default="hb_index")
    args = parser.parse_args()

    if len(args.point) < 2:
        sys.exit("Need at least 2 points to fit a line. Three or more lets you "
                 "judge whether it IS a line.")

    print(f"Reading {args.csv}, column {args.column!r}\n")
    print(f"  {'known g/dL':>11}  {'n':>4}  {'mean index':>11}  {'sd':>8}")

    xs, ys, modes = [], [], set()
    for window, concentration in args.point:
        values, point_modes = collect(args.csv, args.column, window)
        modes |= point_modes
        if not values:
            print(f"  {concentration:>11.2f}     -  no rows in "
                  f"{window[0]}-{window[1]}")
            continue
        mean = statistics.fmean(values)
        sd = statistics.stdev(values) if len(values) > 1 else 0.0
        print(f"  {concentration:>11.2f}  {len(values):>4}  {mean:>11.4f}  {sd:>8.4f}")
        xs.append(mean)
        ys.append(concentration)

    print()
    if len(xs) < 2:
        sys.exit("Not enough windows matched. Check the times against the CSV.")

    if modes and modes != {"absorbance"}:
        print(f"  WARNING: hb_mode is {sorted(modes)}. Only 'absorbance' rows")
        print("  carry a real measurement - calibrate against water first.\n")

    over = [x for x in xs if x > ABSORBANCE_LINEAR_CEILING]
    if over:
        print(f"  WARNING: {len(over)} sample(s) above index "
              f"{ABSORBANCE_LINEAR_CEILING}. Beer-Lambert goes non-linear when")
        print("  almost no light gets through - dilute further, or use a")
        print("  shorter path length, and re-run.\n")

    try:
        slope, intercept, r2 = linear_fit(xs, ys)
    except ValueError as exc:
        sys.exit(f"  {exc}")

    print(f"  fit:  g/dL = {slope:.4f} * index + {intercept:.4f}")
    print(f"  R^2 = {r2:.5f}\n")

    if r2 < MIN_ACCEPTABLE_R2:
        print(f"  R^2 is below {MIN_ACCEPTABLE_R2}, so this is NOT a straight")
        print("  line and the rig is not obeying Beer-Lambert. Common causes:")
        print("    - the cuvette moved between samples (path length changed)")
        print("    - ambient light varied; enclose the sensor")
        print("    - the top samples are too absorbing; dilute and repeat")
        print("    - the sample settled or clotted during the run")
        print("\n  Fix the cause and re-measure. Do not use this fit.")
        sys.exit(1)

    print("  Good linear fit. Apply it with:\n")
    print(f"    set HEMOGUARD_HB_CALIBRATION={slope:.4f},{intercept:.4f}\n")
    print("  Then restart uvicorn. The dashboard will report g/dL instead of")
    print("  the relative index. Valid over the range you measured "
          f"({min(ys):.1f}-{max(ys):.1f} g/dL);")
    print("  readings outside it are extrapolation.")


if __name__ == "__main__":
    main()
