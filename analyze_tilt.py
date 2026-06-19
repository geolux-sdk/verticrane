# coding:UTF-8
# Analyze a tilt log CSV and produce a text report of noise/stability statistics.
#
#   python analyze_tilt.py <path-to-csv>     # prints report and writes <same-name>.txt
#
# Also usable as a module: analyze_tilt.analyze(csv_path) -> report string.

import csv
import math
import os
import sys

# Requirement targets for this project.
ANGLE_REQ_DEG = 0.01     # required tilt accuracy
SLOPE_THRESH_PCT = 0.1   # alarm threshold (1/1000 = 0.1%)
FILTER_SECONDS = 1.0     # moving-average window for the tilt-peak (uprightness) metric


def _stats(x):
    n = len(x)
    m = sum(x) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in x) / n)
    return m, sd, min(x), max(x), max(x) - min(x)


def _moving(x, w, median):
    out = []
    for i in range(len(x)):
        seg = x[max(0, i - w + 1):i + 1]
        if median:
            s = sorted(seg)
            out.append(s[len(s) // 2])
        else:
            out.append(sum(seg) / len(seg))
    return out


def _read(csv_path):
    data = {}
    with open(csv_path, newline="") as f:
        r = csv.DictReader(f)
        for name in r.fieldnames:
            data[name] = []
        for row in r:
            for name in r.fieldnames:
                try:
                    data[name].append(float(row[name]))
                except (ValueError, TypeError):
                    pass
    return data


def analyze(csv_path):
    data = _read(csv_path)
    roll = data.get("Roll_deg", [])
    pitch = data.get("Pitch_deg", [])
    slope = data.get("slope_pct", [])
    elapsed = data.get("elapsed_s", [])
    n = len(slope)
    if n == 0:
        return "No numeric data found in {0}".format(csv_path)

    dur = elapsed[-1] - elapsed[0] if len(elapsed) > 1 else 0.0
    rate = (n - 1) / dur if dur > 0 else 0.0
    w = max(1, int(round(FILTER_SECONDS * rate)))

    L = []
    L.append("Tilt log analysis: {0}".format(os.path.basename(csv_path)))
    L.append("=" * 60)
    L.append("samples : {0}".format(n))
    L.append("duration: {0:.1f} s   rate: {1:.2f} Hz".format(dur, rate))
    L.append("")

    L.append("Per-field statistics")
    L.append("-" * 60)
    L.append("{0:<12}{1:>11}{2:>11}{3:>11}{4:>11}{5:>11}".format(
        "field", "mean", "std", "min", "max", "pk-pk"))
    for fld in ("Roll_deg", "Pitch_deg", "slope_pct",
                "GyroX_dps", "GyroY_dps", "GyroZ_dps", "Temp_C"):
        if data.get(fld):
            m, sd, mn, mx, pp = _stats(data[fld])
            L.append("{0:<12}{1:>11.4f}{2:>11.4f}{3:>11.4f}{4:>11.4f}{5:>11.4f}".format(
                fld, m, sd, mn, mx, pp))
    L.append("")

    # Tilt deviation from the mean orientation (removes the static bench/mount offset).
    rm = sum(roll) / len(roll)
    pm = sum(pitch) / len(pitch)
    dev = sorted(math.hypot(roll[i] - rm, pitch[i] - pm) for i in range(len(roll)))
    p99 = dev[int(0.99 * len(dev))]
    L.append("Tilt deviation from mean orientation (deg)")
    L.append("-" * 60)
    L.append("max={0:.4f}  mean={1:.4f}  p99={2:.4f}".format(
        dev[-1], sum(dev) / len(dev), p99))
    L.append("")

    # Effect of a short filter on the alarm path.
    L.append("Short-filter effect (window {0} samples ~ {1:.1f} s)".format(w, FILTER_SECONDS))
    L.append("-" * 60)
    L.append("{0:<16}{1:>11}{2:>11}".format("slope_pct", "pk-pk", "std"))
    for label, series in (("raw", slope),
                          ("moving-avg", _moving(slope, w, False)),
                          ("moving-median", _moving(slope, w, True))):
        _, sd, _, _, pp = _stats(series)
        L.append("{0:<16}{1:>11.4f}{2:>11.4f}".format(label, pp, sd))
    L.append("")

    # Plain-language assessment against the project targets.
    _, roll_sd, _, _, _ = _stats(roll)
    _, pitch_sd, _, _, _ = _stats(pitch)
    # Uprightness metric: peak of the 1 s moving average of the resultant slope.
    slope_ma = _moving(slope, w, False)
    ma_peak = max(slope_ma)
    L.append("Assessment (targets: {0} deg accuracy, {1}% alarm)".format(
        ANGLE_REQ_DEG, SLOPE_THRESH_PCT))
    L.append("-" * 60)
    L.append("Roll  noise std = {0:.4f} deg  -> {1} (req <= {2})".format(
        roll_sd, "PASS" if 3 * roll_sd <= ANGLE_REQ_DEG else "CHECK (3-sigma > req)", ANGLE_REQ_DEG))
    L.append("Pitch noise std = {0:.4f} deg  -> {1} (req <= {2})".format(
        pitch_sd, "PASS" if 3 * pitch_sd <= ANGLE_REQ_DEG else "CHECK (3-sigma > req)", ANGLE_REQ_DEG))
    L.append("slope raw max            = {0:.4f} %".format(max(slope)))
    L.append("slope {0:.0f}s-avg peak (max)  = {1:.4f} %  -> {2} (vs {3}% threshold)".format(
        FILTER_SECONDS, ma_peak,
        "OVER" if ma_peak > SLOPE_THRESH_PCT else "under", SLOPE_THRESH_PCT))
    L.append("")
    return "\n".join(L)


def main():
    if len(sys.argv) < 2:
        print("usage: python analyze_tilt.py <path-to-csv>")
        return
    csv_path = sys.argv[1]
    report = analyze(csv_path)
    print(report)
    txt_path = os.path.splitext(csv_path)[0] + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print("Report written to {0}".format(txt_path))


if __name__ == "__main__":
    main()
