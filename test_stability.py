# coding:UTF-8
# Self-check for the stability judge.
#
#   python test_stability.py
#
# The interesting cases are the ones that are easy to get wrong: the 0/360
# angle seam, a window that has not filled yet, and making sure real motion is
# actually rejected rather than averaged away.

from __future__ import annotations

import math
import sys

import stability as st

_passed: int = 0
_failed: int = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print("  PASS  {0}".format(label))
    else:
        _failed += 1
        print("  FAIL  {0}{1}".format(label, "  <- " + detail if detail else ""))


def still_samples(n: int = 150, rate: float = 25.0, noise: float = 0.0,
                  yaw: float = 0.0) -> list[st.Sample]:
    """A sensor sitting still, optionally with a little sinusoidal noise."""
    out: list[st.Sample] = []
    for i in range(n):
        w: float = noise * math.sin(i * 0.7)
        out.append(st.Sample(
            t=i / rate, roll=w, pitch=w, yaw=(yaw + w) % 360.0,
            acc=(w, w, 1.0 + w), gyro=(w, w, w),
        ))
    return out


def test_circular() -> None:
    print("\n[1] 순환 표준편차")
    # The whole reason this function exists: a plain stdev of these two values
    # reports ~180 degrees when the real spread is 0.2.
    seam: list[float] = [359.9, 0.1] * 50
    plain: float = st._pstdev(seam)
    circ: float = st.circular_stdev_deg(seam)
    check("plain stdev is misleading at the seam", plain > 100.0, "{0:.1f}".format(plain))
    check("circular stdev stays small", circ < 1.0, "{0:.4f}".format(circ))

    # Away from the seam it must agree with the ordinary calculation.
    mid: list[float] = [180.0 + 0.1 * math.sin(i) for i in range(100)]
    check("matches linear away from the seam",
          abs(st.circular_stdev_deg(mid) - st._pstdev(mid)) < 1e-3)

    check("constant series is exactly zero", st.circular_stdev_deg([42.0] * 50) == 0.0)
    check("no negative zero", math.copysign(1.0, st.circular_stdev_deg([0.0] * 50)) > 0)
    check("single value is zero", st.circular_stdev_deg([7.0]) == 0.0)


def test_still_is_stable() -> None:
    print("\n[2] 정지 상태")
    limits = st.Limits()
    verdict = st.evaluate(still_samples(), limits)
    check("perfectly still is stable", verdict.stable, verdict.reason)
    check("all metrics reported", len(verdict.metrics) == len(st.METRICS))
    check("every metric passes", all(m.ok for m in verdict.metrics))

    # Same thing sitting on the yaw seam -- must not be judged differently.
    seam = st.evaluate(still_samples(yaw=359.95), limits)
    check("still at the 0/360 seam is stable", seam.stable, seam.reason)


def test_motion_is_rejected() -> None:
    print("\n[3] 움직이는 상태")
    limits = st.Limits()

    spinning: list[st.Sample] = [
        st.Sample(t=i / 25.0, roll=0.0, pitch=0.0, yaw=0.0,
                  acc=(0.0, 0.0, 1.0), gyro=(5.0, 0.0, 0.0))
        for i in range(150)
    ]
    v1 = st.evaluate(spinning, limits)
    check("steady rotation rejected", not v1.stable)
    check("blamed on the gyro", "자이로" in v1.reason, v1.reason)

    swinging: list[st.Sample] = [
        st.Sample(t=i / 25.0, roll=3.0 * math.sin(i * 0.2), pitch=0.0, yaw=0.0,
                  acc=(0.0, 0.0, 1.0), gyro=(0.0, 0.0, 0.0))
        for i in range(150)
    ]
    v2 = st.evaluate(swinging, limits)
    check("swinging attitude rejected", not v2.stable)
    check("blamed on attitude", "자세각" in v2.reason, v2.reason)

    shaking: list[st.Sample] = [
        st.Sample(t=i / 25.0, roll=0.0, pitch=0.0, yaw=0.0,
                  acc=(0.1 * math.sin(i), 0.0, 1.0), gyro=(0.0, 0.0, 0.0))
        for i in range(150)
    ]
    v3 = st.evaluate(shaking, limits)
    check("vibration rejected", not v3.stable)
    check("blamed on acceleration", "가속도" in v3.reason, v3.reason)


def test_min_samples() -> None:
    print("\n[4] 최소 샘플 수")
    limits = st.Limits(min_samples=100)
    verdict = st.evaluate(still_samples(n=50), limits)
    check("short window is not stable", not verdict.stable)
    check("reason says samples are short", "샘플 부족" in verdict.reason, verdict.reason)
    check("no metrics computed yet", verdict.metrics == [])
    check("empty window is safe", not st.evaluate([], limits).stable)


def test_monitor_window() -> None:
    print("\n[5] 롤링 윈도우")
    monitor = st.StabilityMonitor(st.Limits(window_seconds=5.0, min_samples=100))
    for sample in still_samples(n=500):     # 20 s at 25 Hz
        monitor.add(sample)
    # 5 s at 25 Hz is 125 samples; the deque must not keep growing.
    check("window is bounded", 120 <= len(monitor) <= 130, str(len(monitor)))
    verdict = monitor.evaluate()
    check("evaluates stable", verdict.stable, verdict.reason)
    check("span is about the window", 4.5 <= verdict.span_s <= 5.5,
          "{0:.2f}".format(verdict.span_s))

    monitor.reset()
    check("reset clears the window", len(monitor) == 0)
    check("reset clears the verdict", monitor.last is None)


def test_recovery_after_motion() -> None:
    print("\n[6] 움직임 후 회복")
    monitor = st.StabilityMonitor(st.Limits(window_seconds=5.0, min_samples=100))
    for i in range(150):    # 6 s of motion
        monitor.add(st.Sample(t=i / 25.0, roll=0.0, pitch=0.0, yaw=0.0,
                              acc=(0.0, 0.0, 1.0), gyro=(9.0, 0.0, 0.0)))
    check("unstable while moving", not monitor.evaluate().stable)

    for i in range(150, 350):   # 8 s of stillness -- motion falls out of the window
        monitor.add(st.Sample(t=i / 25.0, roll=0.0, pitch=0.0, yaw=0.0,
                              acc=(0.0, 0.0, 1.0), gyro=(0.0, 0.0, 0.0)))
    check("stable once motion leaves the window", monitor.evaluate().stable)


def test_config_and_report() -> None:
    print("\n[7] 설정과 보고")
    limits = st.Limits.from_config({"recorder": {"gyro_rms_max_dps": 0.05,
                                                 "stability_min_samples": 10}})
    check("config value applied", limits.gyro_rms_max_dps == 0.05)
    check("unspecified key keeps its default",
          limits.accel_std_max_g == st.DEFAULTS["accel_std_max_g"])
    check("empty config is fine", st.Limits.from_config({}).min_samples == 100)

    verdict = st.evaluate(still_samples(), st.Limits())
    payload = verdict.as_dict()
    check("serialises for /api/status", payload["stable"] is True)
    check("carries measured values and limits",
          all({"value", "limit", "ok", "label"} <= set(m) for m in payload["metrics"]))
    check("summary is printable", "STABLE" in verdict.summary(), verdict.summary())


def main() -> int:
    test_circular()
    test_still_is_stable()
    test_motion_is_rejected()
    test_min_samples()
    test_monitor_window()
    test_recovery_after_motion()
    test_config_and_report()
    total: int = _passed + _failed
    print("\n{0}/{1} 통과".format(_passed, total))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
