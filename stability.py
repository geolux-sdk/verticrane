# coding:UTF-8
# Decides when the sensor has settled enough to start recording (section 4).
#
#   python stability.py data/tilt_log_20260619_114626.csv
#
# Recording must not begin while the crane is still swinging, so the recorder
# feeds live samples in and asks for a verdict. Every metric reports its
# measured value alongside its limit, because /api/status has to be able to
# answer "why has recording not started yet".
#
# Run as a script against an existing tilt CSV to see whether the configured
# limits are sensible for real data before trusting them in the field.

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from loguru import logger

DEFAULTS: dict[str, Any] = {
    "stability_window_seconds": 5.0,
    "stability_min_samples": 100,
    "gyro_rms_max_dps": 0.5,
    "accel_std_max_g": 0.01,
    "attitude_std_max_deg": 0.3,
}


@dataclass
class Sample:
    t: float                       # monotonic seconds
    roll: float
    pitch: float
    yaw: float
    acc: tuple[float, float, float]
    gyro: tuple[float, float, float]


@dataclass
class Limits:
    window_seconds: float = float(DEFAULTS["stability_window_seconds"])
    min_samples: int = int(DEFAULTS["stability_min_samples"])
    gyro_rms_max_dps: float = float(DEFAULTS["gyro_rms_max_dps"])
    accel_std_max_g: float = float(DEFAULTS["accel_std_max_g"])
    attitude_std_max_deg: float = float(DEFAULTS["attitude_std_max_deg"])

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Limits":
        # Recorder settings live under a "recorder" key in config.json so they
        # sit beside the existing dashboard settings (section 1).
        rec: dict[str, Any] = dict(DEFAULTS)
        rec.update(cfg.get("recorder", {}) or {})
        return cls(
            window_seconds=float(rec["stability_window_seconds"]),
            min_samples=int(rec["stability_min_samples"]),
            gyro_rms_max_dps=float(rec["gyro_rms_max_dps"]),
            accel_std_max_g=float(rec["accel_std_max_g"]),
            attitude_std_max_deg=float(rec["attitude_std_max_deg"]),
        )


@dataclass
class MetricResult:
    key: str
    label: str
    value: float
    limit: float
    unit: str
    ok: bool

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "unit": self.unit,
                "value": round(self.value, 6), "limit": self.limit, "ok": self.ok}


@dataclass
class Verdict:
    stable: bool
    reason: str
    samples: int
    span_s: float
    metrics: list[MetricResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"stable": self.stable, "reason": self.reason,
                "samples": self.samples, "span_s": round(self.span_s, 3),
                "metrics": [m.as_dict() for m in self.metrics]}

    def summary(self) -> str:
        parts: list[str] = ["{0}={1:.4g}{2}{3}".format(
            m.key, m.value, m.unit, "" if m.ok else "!") for m in self.metrics]
        return "{0} ({1})".format("STABLE" if self.stable else "UNSTABLE", " ".join(parts))


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def _pstdev(values: Iterable[float]) -> float:
    seq: list[float] = list(values)
    if len(seq) < 2:
        return 0.0
    mean: float = sum(seq) / len(seq)
    return math.sqrt(sum((v - mean) ** 2 for v in seq) / len(seq))


def circular_stdev_deg(angles_deg: Iterable[float]) -> float:
    """Angular spread that does not blow up across the 0/360 boundary.

    A plain standard deviation of [359.9, 0.1] reports ~180 degrees when the
    real spread is 0.2. Yaw crosses that seam constantly, so the dispersion is
    taken from the mean resultant length instead (section 4).
    """
    seq: list[float] = list(angles_deg)
    if len(seq) < 2:
        return 0.0
    radians: list[float] = [math.radians(a) for a in seq]
    c: float = sum(math.cos(r) for r in radians) / len(radians)
    s: float = sum(math.sin(r) for r in radians) / len(radians)
    r_len: float = math.hypot(c, s)
    # Rounding can push R a hair above 1 for a near-constant series, which
    # would take the sqrt of a negative number.
    r_len = min(max(r_len, 1e-12), 1.0)
    # abs() keeps a constant series from reporting -0.0 (sqrt of negative zero).
    return abs(math.degrees(math.sqrt(-2.0 * math.log(r_len))))


def _rms(values: Iterable[float]) -> float:
    seq: list[float] = list(values)
    if not seq:
        return 0.0
    return math.sqrt(sum(v * v for v in seq) / len(seq))


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

@dataclass
class Metric:
    """One stability criterion. Add an entry to METRICS to add a criterion."""
    key: str
    label: str
    unit: str
    limit_attr: str
    compute: Callable[[list[Sample]], float]


def _gyro_rms(window: list[Sample]) -> float:
    # Rotation rate should sit at the noise floor when nothing is moving.
    # Taken across all three axes at once: swing about any axis disqualifies.
    return _rms([g for s in window for g in s.gyro])


def _accel_std(window: list[Sample]) -> float:
    # Gravity dominates the mean, so the spread is what carries the motion.
    return max(_pstdev([s.acc[i] for s in window]) for i in range(3))


def _attitude_std(window: list[Sample]) -> float:
    # Circular for all three: roll wraps at +/-180 just as yaw does at 0/360,
    # and for the small spreads seen at rest it matches the linear result.
    return max(
        circular_stdev_deg([s.roll for s in window]),
        circular_stdev_deg([s.pitch for s in window]),
        circular_stdev_deg([s.yaw for s in window]),
    )


METRICS: list[Metric] = [
    Metric("gyro_rms", "자이로 RMS", " °/s", "gyro_rms_max_dps", _gyro_rms),
    Metric("accel_std", "가속도 표준편차", " g", "accel_std_max_g", _accel_std),
    Metric("attitude_std", "자세각 표준편차", " °", "attitude_std_max_deg", _attitude_std),
]


def evaluate(window: list[Sample], limits: Limits) -> Verdict:
    """Judge one window of samples. Pure function -- easy to test and to reuse."""
    span: float = (window[-1].t - window[0].t) if len(window) >= 2 else 0.0
    if len(window) < limits.min_samples:
        return Verdict(False, "샘플 부족 ({0}/{1})".format(len(window), limits.min_samples),
                       len(window), span)

    results: list[MetricResult] = []
    for metric in METRICS:
        limit: float = float(getattr(limits, metric.limit_attr))
        value: float = metric.compute(window)
        results.append(MetricResult(metric.key, metric.label, value,
                                    limit, metric.unit, value <= limit))

    failed: list[MetricResult] = [m for m in results if not m.ok]
    if failed:
        reason: str = ", ".join("{0} {1:.4g} > {2:g}".format(m.label, m.value, m.limit)
                                for m in failed)
        return Verdict(False, reason, len(window), span, results)
    return Verdict(True, "안정", len(window), span, results)


# --------------------------------------------------------------------------
# Rolling monitor
# --------------------------------------------------------------------------

class StabilityMonitor:
    """Keeps the trailing window and answers on demand.

    The recorder pushes every sample it polls; only samples inside the window
    are retained, so memory stays bounded regardless of how long it waits.
    """

    def __init__(self, limits: Optional[Limits] = None) -> None:
        self.limits: Limits = limits or Limits()
        self._window: deque[Sample] = deque()
        self.last: Optional[Verdict] = None

    def add(self, sample: Sample) -> None:
        self._window.append(sample)
        cutoff: float = sample.t - self.limits.window_seconds
        while self._window and self._window[0].t < cutoff:
            self._window.popleft()

    def evaluate(self) -> Verdict:
        self.last = evaluate(list(self._window), self.limits)
        return self.last

    def reset(self) -> None:
        self._window.clear()
        self.last = None

    def __len__(self) -> int:
        return len(self._window)


# --------------------------------------------------------------------------
# CSV replay -- validate limits against data already recorded
# --------------------------------------------------------------------------

_CSV_COLUMNS: tuple[str, ...] = (
    "elapsed_s", "Roll_deg", "Pitch_deg", "Yaw_deg",
    "AccX_g", "AccY_g", "AccZ_g", "GyroX_dps", "GyroY_dps", "GyroZ_dps",
)


def load_csv(path: str) -> list[Sample]:
    """Read a log_tilt.py CSV into samples, skipping rows with gaps."""
    samples: list[Sample] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing: list[str] = [c for c in _CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError("CSV lacks columns: {0}".format(", ".join(missing)))
        for row in reader:
            try:
                values: list[float] = [float(row[c]) for c in _CSV_COLUMNS]
            except (TypeError, ValueError):
                continue  # a dropped Modbus read leaves blanks
            samples.append(Sample(
                t=values[0], roll=values[1], pitch=values[2], yaw=values[3],
                acc=(values[4], values[5], values[6]),
                gyro=(values[7], values[8], values[9]),
            ))
    return samples


def replay(path: str, limits: Limits, step: int = 25) -> None:
    """Slide the window across a CSV and report how the limits behave."""
    samples: list[Sample] = load_csv(path)
    if not samples:
        logger.error("{}: no usable rows", os.path.basename(path))
        return

    monitor = StabilityMonitor(limits)
    verdicts: list[Verdict] = []
    for i, sample in enumerate(samples):
        monitor.add(sample)
        if i % step == 0 and len(monitor) >= limits.min_samples:
            verdicts.append(monitor.evaluate())

    if not verdicts:
        logger.warning("{}: never reached {} samples in a {:g}s window",
                       os.path.basename(path), limits.min_samples, limits.window_seconds)
        return

    duration: float = samples[-1].t - samples[0].t
    stable: int = sum(1 for v in verdicts if v.stable)
    print("\n{0}".format(os.path.basename(path)))
    print("  {0} samples, {1:.1f} s, {2:.1f} Hz".format(
        len(samples), duration, len(samples) / duration if duration else 0.0))
    print("  안정 판정: {0}/{1} 창 ({2:.1f} %)".format(
        stable, len(verdicts), 100.0 * stable / len(verdicts)))

    # The distribution matters more than the pass rate: it shows how much head-
    # room the limits actually have against this sensor in this installation.
    print("  {0:<18}{1:>10}{2:>10}{3:>10}{4:>10}  {5}".format(
        "지표", "최소", "중앙", "최대", "기준", "판정"))
    for idx, metric in enumerate(METRICS):
        values: list[float] = sorted(v.metrics[idx].value for v in verdicts if v.metrics)
        if not values:
            continue
        limit: float = float(getattr(limits, metric.limit_attr))
        median: float = values[len(values) // 2]
        passed: int = sum(1 for v in values if v <= limit)
        print("  {0:<18}{1:>10.4g}{2:>10.4g}{3:>10.4g}{4:>10g}  {5:.0f} %".format(
            metric.key, values[0], median, values[-1], limit, 100.0 * passed / len(values)))

    worst: Verdict = max(verdicts, key=lambda v: 0 if v.stable else 1)
    if not worst.stable:
        print("  대표적 실패 사유: {0}".format(worst.reason))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check the stability limits against recorded tilt CSVs.")
    parser.add_argument("csv", nargs="*", help="CSV files (default: every data/*.csv).")
    parser.add_argument("--window", type=float, default=DEFAULTS["stability_window_seconds"])
    parser.add_argument("--min-samples", type=int, default=DEFAULTS["stability_min_samples"])
    parser.add_argument("--gyro-rms", type=float, default=DEFAULTS["gyro_rms_max_dps"])
    parser.add_argument("--accel-std", type=float, default=DEFAULTS["accel_std_max_g"])
    parser.add_argument("--attitude-std", type=float, default=DEFAULTS["attitude_std_max_deg"])
    args = parser.parse_args()

    limits = Limits(window_seconds=args.window, min_samples=args.min_samples,
                    gyro_rms_max_dps=args.gyro_rms, accel_std_max_g=args.accel_std,
                    attitude_std_max_deg=args.attitude_std)

    paths: list[str] = args.csv
    if not paths:
        import glob
        paths = sorted(glob.glob(os.path.join("data", "*.csv")))
    if not paths:
        logger.error("No CSV files given and none found in data/")
        return 1

    print("기준: 창 {0:g}s, 최소 {1} 샘플, 자이로 {2:g} °/s, 가속도 {3:g} g, 자세 {4:g} °".format(
        limits.window_seconds, limits.min_samples, limits.gyro_rms_max_dps,
        limits.accel_std_max_g, limits.attitude_std_max_deg))
    for path in paths:
        try:
            replay(path, limits)
        except (OSError, ValueError) as exc:
            logger.error("{}: {}", os.path.basename(path), exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
