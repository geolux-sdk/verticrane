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

import numpy as np

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


def _top_peaks(amp, k):
    # Indices of the k strongest local maxima, excluding the DC bin.
    ac = amp.copy()
    ac[0] = 0.0
    maxima = [i for i in range(1, len(ac) - 1)
              if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1]]
    if not maxima and len(ac) > 1:
        maxima = [int(np.argmax(ac))]
    maxima.sort(key=lambda i: ac[i], reverse=True)
    return maxima[:k]


def _read(csv_path):
    with open(csv_path, newline="") as f:
        r = csv.DictReader(f)
        fields = r.fieldnames or []
        data = {name: [] for name in fields}
        for row in r:
            # One row = one sample: parse all columns together and skip the whole
            # row if any value is missing/non-numeric, so every column stays the
            # same length (later code cross-indexes roll[i]/pitch[i]).
            try:
                vals = [float(row[name]) for name in fields]
            except (ValueError, TypeError):
                continue
            for name, v in zip(fields, vals):
                data[name].append(v)
    return data


def analyze(csv_path):
    data = _read(csv_path)
    roll = data.get("Roll_deg", [])
    pitch = data.get("Pitch_deg", [])
    slope = data.get("slope_pct", [])
    elapsed = data.get("elapsed_s", [])
    n = len(slope)
    if n == 0:
        return "{0} 에서 숫자 데이터를 찾을 수 없습니다".format(csv_path)

    dur = elapsed[-1] - elapsed[0] if len(elapsed) > 1 else 0.0
    rate = (n - 1) / dur if dur > 0 else 0.0
    w = max(1, int(round(FILTER_SECONDS * rate)))

    L = []
    L.append("기울기 로그 분석: {0}".format(os.path.basename(csv_path)))
    L.append("=" * 60)
    L.append("샘플 수 : {0}".format(n))
    L.append("측정 시간: {0:.1f} s   샘플링: {1:.2f} Hz".format(dur, rate))
    L.append("")

    L.append("항목별 통계")
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
    L.append("평균 자세 대비 기울기 편차 (deg)")
    L.append("-" * 60)
    if roll and pitch:
        m = min(len(roll), len(pitch))
        rm = sum(roll[:m]) / m
        pm = sum(pitch[:m]) / m
        dev = sorted(math.hypot(roll[i] - rm, pitch[i] - pm) for i in range(m))
        p99 = dev[min(int(0.99 * len(dev)), len(dev) - 1)]
        L.append("최대={0:.4f}  평균={1:.4f}  p99={2:.4f}".format(
            dev[-1], sum(dev) / len(dev), p99))
    else:
        L.append("Roll_deg / Pitch_deg 데이터가 없어 계산할 수 없습니다")
    L.append("")

    # Effect of a short filter on the alarm path.
    L.append("단기 필터 효과 (윈도우 {0} 샘플 ~ {1:.1f} s)".format(w, FILTER_SECONDS))
    L.append("-" * 60)
    L.append("{0:<16}{1:>11}{2:>11}".format("slope_pct", "pk-pk", "std"))
    for label, series in (("원본", slope),
                          ("이동평균", _moving(slope, w, False)),
                          ("이동중앙값", _moving(slope, w, True))):
        _, sd, _, _, pp = _stats(series)
        L.append("{0:<16}{1:>11.4f}{2:>11.4f}".format(label, pp, sd))
    L.append("")

    # Sway spectrum: dominant frequencies (FFT of slope).
    L.append("흔들림 스펙트럼 (기울기 FFT) - 상위 3개 피크")
    L.append("-" * 60)
    if n > 2 and rate > 0:
        s = np.asarray(slope, dtype=float)
        s = s - s.mean()
        win = np.hanning(len(s))
        amp = np.abs(np.fft.rfft(s * win))
        freq = np.fft.rfftfreq(len(s), d=1.0 / rate)
        # Single-sided physical amplitude in slope %, corrected for the Hann
        # window's coherent gain (sum of window), then the matching tilt angle.
        amp_pct = 2.0 * amp / win.sum()
        top = _top_peaks(amp, 3)
        if top:
            for rank, i in enumerate(top, 1):
                period = 1.0 / freq[i] if freq[i] > 0 else float("inf")
                deg = math.degrees(math.atan(amp_pct[i] / 100.0))
                L.append("#{0}  {1:.3f} Hz  (주기 {2:.2f} s)  진폭 {3:.4f} % ({4:.4f} deg)".format(
                    rank, freq[i], period, amp_pct[i], deg))
        else:
            L.append("뚜렷한 스펙트럼 피크 없음")
    else:
        L.append("스펙트럼 분석에 데이터가 부족함")
    L.append("")

    # Plain-language assessment against the project targets.
    # Uprightness metric: peak of the 1 s moving average of the resultant slope.
    slope_ma = _moving(slope, w, False)
    ma_peak = max(slope_ma)
    L.append("평가 (목표: 정확도 {0} deg, 경보 {1}%)".format(
        ANGLE_REQ_DEG, SLOPE_THRESH_PCT))
    L.append("-" * 60)
    if roll:
        _, roll_sd, _, _, _ = _stats(roll)
        L.append("Roll  노이즈 표준편차 = {0:.4f} deg  -> {1} (요구 <= {2})".format(
            roll_sd, "통과" if 3 * roll_sd <= ANGLE_REQ_DEG else "점검 (3시그마 > 요구)", ANGLE_REQ_DEG))
    if pitch:
        _, pitch_sd, _, _, _ = _stats(pitch)
        L.append("Pitch 노이즈 표준편차 = {0:.4f} deg  -> {1} (요구 <= {2})".format(
            pitch_sd, "통과" if 3 * pitch_sd <= ANGLE_REQ_DEG else "점검 (3시그마 > 요구)", ANGLE_REQ_DEG))
    L.append("기울기 원본 최대         = {0:.4f} %".format(max(slope)))
    L.append("기울기 {0:.0f}초평균 피크(최대) = {1:.4f} %  -> {2} (임계값 {3}% 대비)".format(
        FILTER_SECONDS, ma_peak,
        "초과" if ma_peak > SLOPE_THRESH_PCT else "이내", SLOPE_THRESH_PCT))
    L.append("")
    return "\n".join(L)


def main():
    if len(sys.argv) < 2:
        print("사용법: python analyze_tilt.py <CSV 경로>")
        return
    csv_path = sys.argv[1]
    report = analyze(csv_path)
    print(report)
    txt_path = os.path.splitext(csv_path)[0] + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print("리포트를 {0} 에 저장했습니다".format(txt_path))


if __name__ == "__main__":
    main()
