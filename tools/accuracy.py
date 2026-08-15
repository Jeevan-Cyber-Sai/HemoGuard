"""Accuracy and precision analysis for HemoGuard.

Two different questions, two different modes, and they are not interchangeable.

PRECISION  (--precision)  How repeatable is a channel?
    Needs only a recorded log of a stable sample. Reports the within-window
    standard deviation, which bounds how well the instrument can ever agree
    with anything - a channel that cannot repeat itself cannot be accurate.

ACCURACY   (--compare)   How close is it to the truth?
    Needs PAIRED reference values, and nothing can substitute for them. Reports
    bias, 95% limits of agreement (Bland-Altman - the standard for comparing a
    new method against an established one), RMSE and a regression fit.

    python tools/accuracy.py --compare pairs.csv

    pairs.csv:
        reference,measured
        81.0,80.7
        50.0,49.6
        ...
"""

import argparse
import csv
import math
import statistics
import sys


# A window this long at 1 Hz is enough to characterise noise without drifting
# into a genuinely different sample.
WINDOW = 20


def read_column(path, column):
    out = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw = row.get(column, "")
            if raw in ("", None):
                continue
            try:
                out.append((row.get("timestamp", ""), float(raw)))
            except ValueError:
                continue
    return out


def precision(path, column, window=WINDOW):
    series = read_column(path, column)
    if not series:
        print(f"  {column}: no data")
        return

    values = [v for _, v in series]

    # Consecutive identical values are the SAME reading repeated - the node
    # holds a figure between updates and the backend rebroadcasts it every
    # second. Counting those as independent samples drives the apparent noise
    # to exactly zero and would report perfect precision for a channel that
    # never moved. Only transitions are real measurements.
    fresh = [values[0]]
    for v in values[1:]:
        if v != fresh[-1]:
            fresh.append(v)

    held = len(values) - len(fresh)

    print(f"  {column}")
    print(f"    rows logged       {len(values)}  ({held} repeats of the previous value)")
    print(f"    distinct readings {len(fresh)}")
    print(f"    full range        {min(values):.4f} .. {max(values):.4f}")

    if len(fresh) < 8:
        print("    too few distinct readings to characterise noise")
        return

    # Successive differences, not a plain SD over a window: it cancels slow
    # drift, so what is left is short-term instrument noise rather than the
    # sample genuinely changing. Divided by sqrt(2) because the difference of
    # two independent readings carries twice the variance of one.
    diffs = [abs(b - a) for a, b in zip(fresh, fresh[1:])]
    sd = statistics.pstdev(diffs) / math.sqrt(2)
    typical = statistics.median(diffs)

    # Quietest stretch, over distinct readings only.
    best = None
    if len(fresh) >= window:
        for i in range(len(fresh) - window + 1):
            chunk = fresh[i:i + window]
            csd = statistics.pstdev(chunk)
            if best is None or csd < best[0]:
                best = (csd, statistics.fmean(chunk))

    print(f"    short-term noise  SD {sd:.4f}   (median step {typical:.4f})")
    print(f"    repeatability     +/- {1.96 * sd:.4f}  (95%)")
    if best:
        qsd, qmean = best
        print(f"    quietest {window}       mean {qmean:.4f}   SD {qsd:.4f}")
        if qmean:
            print(f"    coefficient of variation  {qsd / qmean * 100.0:.2f}%")


def compare(path):
    refs, meas = [], []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                refs.append(float(row["reference"]))
                meas.append(float(row["measured"]))
            except (KeyError, ValueError, TypeError):
                continue

    n = len(refs)
    if n < 3:
        sys.exit("Need at least 3 paired values. Columns: reference,measured")

    diffs = [m - r for m, r in zip(meas, refs)]
    bias = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0

    # Bland-Altman: the interval within which 95% of differences between this
    # instrument and the reference are expected to fall. A correlation
    # coefficient would look better and say less - two methods can correlate
    # perfectly while disagreeing by a constant offset.
    lo, hi = bias - 1.96 * sd, bias + 1.96 * sd

    rmse = math.sqrt(statistics.fmean([d * d for d in diffs]))
    pct = [abs(d) / r * 100.0 for d, r in zip(diffs, refs) if r]
    mape = statistics.fmean(pct) if pct else float("nan")

    mean_r = statistics.fmean(refs)
    mean_m = statistics.fmean(meas)
    sxx = sum((r - mean_r) ** 2 for r in refs)
    sxy = sum((r - mean_r) * (m - mean_m) for r, m in zip(refs, meas))
    slope = sxy / sxx if sxx else float("nan")
    intercept = mean_m - slope * mean_r
    ss_tot = sum((m - mean_m) ** 2 for m in meas)
    ss_res = sum((m - (slope * r + intercept)) ** 2 for r, m in zip(refs, meas))
    r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")

    print(f"\n  n = {n}   reference range {min(refs):g} .. {max(refs):g}\n")
    print(f"  bias (mean error)      {bias:+.4f}")
    print(f"  SD of differences       {sd:.4f}")
    print(f"  95% limits of agreement {lo:+.4f} .. {hi:+.4f}")
    print(f"  RMSE                    {rmse:.4f}")
    print(f"  mean absolute % error   {mape:.2f}%")
    print(f"  fit  measured = {slope:.4f} x reference {intercept:+.4f}")
    print(f"  R^2                     {r2:.5f}\n")

    # A systematic offset and a scale error need different corrections, so they
    # are worth separating rather than reporting one "accuracy" figure.
    if abs(bias) > 2 * sd / math.sqrt(n):
        print(f"  There is a systematic offset of {bias:+.3f}. Subtracting it")
        print(f"  would remove most of the error - check the tare or the")
        print(f"  dry-pad setting rather than the calibration slope.")
    if abs(slope - 1.0) > 0.05:
        print(f"  The slope is {slope:.3f}, not 1.0, so the error grows with the")
        print(f"  reading. That is a calibration-factor problem: scale the")
        print(f"  factor by {1/slope:.4f}.")
    if abs(bias) <= 2 * sd / math.sqrt(n) and abs(slope - 1.0) <= 0.05:
        print("  No significant offset and slope is within 5% of 1.0 - the")
        print("  remaining error is random scatter, not a correctable bias.")


def main():
    parser = argparse.ArgumentParser(description="HemoGuard accuracy / precision")
    parser.add_argument("--precision", metavar="LOG.csv",
                        help="repeatability from a recorded log")
    parser.add_argument("--columns", default="weight_g,hb_index,absorbance,spo2,pulse_bpm")
    parser.add_argument("--window", type=int, default=WINDOW)
    parser.add_argument("--compare", metavar="PAIRS.csv",
                        help="accuracy against paired reference values")
    args = parser.parse_args()

    if not args.precision and not args.compare:
        parser.error("give --precision LOG.csv or --compare PAIRS.csv")

    if args.precision:
        print(f"\nPRECISION  {args.precision}")
        print("(repeatability only - says nothing about closeness to truth)\n")
        for column in args.columns.split(","):
            precision(args.precision, column.strip(), args.window)
            print()

    if args.compare:
        print(f"\nACCURACY  {args.compare}")
        compare(args.compare)


if __name__ == "__main__":
    main()
