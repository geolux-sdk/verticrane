# coding:UTF-8
# Read the HWT9037-485 and the SCL3300 at the same time and say how they differ.
#
#   python sensor_compare.py                    30 s at 10 Hz, live table
#   python sensor_compare.py --seconds 120      longer, for drift
#   python sensor_compare.py --csv /tmp/cmp.csv keep every sample
#   python sensor_compare.py --quiet            summary only
#
# The two live on separate buses -- Modbus on /dev/serial0, SPI on CE1 -- so
# neither waits for the other.
#
# Comparing them is less obvious than it looks, because their angle outputs do
# not mean the same thing. The HWT9037 reports fused Euler angles; the SCL3300
# reports each axis's inclination from horizontal. Subtracting one from the
# other measures the mounting, not the sensors. So this tool leans on three
# things that survive not knowing how either part is bolted down:
#
#   |acc| against gravity   Both must read 1.000 g at rest whatever their
#                           orientation. The gap is sensitivity and offset
#                           error, and it is the only absolute reference
#                           available without a calibrated tilt table.
#
#   noise while still       Standard deviation over a stationary run. Says
#                           nothing about mounting and everything about which
#                           part you would rather trust at 0.01 degrees.
#
#   tilt from a reference   The angle between each sensor's current
#                           acceleration vector and its own average at the
#                           start of the run. That is the magnitude of the
#                           rotation the part has undergone, and rotation
#                           magnitude is the same physical number for both
#                           however they are mounted relative to each other.
#                           It needs no axis mapping, and it is the one figure
#                           here that tests scale rather than just noise --
#                           but only if the assembly is actually tilted during
#                           the run, and only if the two are rigid with
#                           respect to each other.
#
# What the first runs found, so nobody has to rediscover it. The HWT9037's angle
# output does not average down at all -- 0.0070 deg at a window of one sample and
# 0.0070 deg at a hundred -- which means consecutive samples carry no independent
# information and the figure is its filter's floor, not its noise. It also reads
# an |acc| sigma five times below its own quantisation step, which is impossible
# for anything unfiltered. The SCL3300 tracks 1/sqrt(N) down to about 0.005 deg,
# so the two meet near a 2.5 Hz bandwidth and the SCL3300 is the better of the
# two below that. Raw sigma alone says the opposite, and is wrong.

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from typing import Optional

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import port_config
import read_status
import scl3300

ANGLE_BLOCK = (0x34, 15)
TEMP_REG = (0x43, 1)

# The HWT9037 is stuck at the 16 g range on this unit, so its accelerometer
# resolution is fixed. The SCL3300's depends on the mode.
_HWT_RESOLUTION_MG = 16.0 / 32768.0 * 1000.0
_SCL_RESOLUTION_MG = {scl3300.MODE_1: 1000.0 / 6000.0, scl3300.MODE_2: 1000.0 / 3000.0,
                      scl3300.MODE_3: 1000.0 / 12000.0, scl3300.MODE_4: 1000.0 / 12000.0}


def norm(v: tuple[float, float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def angle_between(a: tuple[float, float, float],
                  b: tuple[float, float, float]) -> float:
    """Degrees between two vectors, clamped so rounding cannot escape acos."""
    na, nb = norm(a), norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    dot = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def span(xs: list[float]) -> float:
    return (max(xs) - min(xs)) if xs else 0.0


class Column:
    """Everything collected from one sensor, so the report can treat them alike."""

    def __init__(self, name: str, resolution_mg: float) -> None:
        self.name = name
        self.resolution_mg = resolution_mg
        self.acc: list[tuple[float, float, float]] = []
        self.ang: list[tuple[float, float, float]] = []
        self.temp: list[float] = []
        self.magnitude: list[float] = []
        self.tilt: list[float] = []
        self.reference: Optional[tuple[float, float, float]] = None
        self.misses = 0

    def add(self, acc: tuple[float, float, float],
            ang: tuple[float, float, float], temp: Optional[float]) -> None:
        self.acc.append(acc)
        self.ang.append(ang)
        self.magnitude.append(norm(acc))
        if temp is not None:
            self.temp.append(temp)

    def set_reference(self, n: int) -> None:
        """Average the opening samples into the orientation everything is measured from.

        Averaging rather than taking one sample: a single frame carries the full
        noise of the part, and that noise would then sit in every tilt figure for
        the rest of the run.
        """
        head = self.acc[:n]
        if not head:
            return
        self.reference = (mean([a[0] for a in head]), mean([a[1] for a in head]),
                          mean([a[2] for a in head]))

    def compute_tilt(self) -> None:
        if self.reference is None:
            return
        self.tilt = [angle_between(a, self.reference) for a in self.acc]


def read_hwt(device) -> Optional[tuple[tuple[float, float, float],
                                       tuple[float, float, float]]]:
    if device.readReg(*ANGLE_BLOCK) is None:
        return None
    g = device.get
    try:
        acc = (g("AccX"), g("AccY"), g("AccZ"))
        ang = (g("AngX"), g("AngY"), g("AngZ"))
    except Exception:                                     # noqa: BLE001
        return None
    if any(v is None for v in acc + ang):
        return None
    return acc, ang                                       # type: ignore[return-value]


def block_average(xs: list[float], n: int) -> list[float]:
    return [sum(xs[i:i + n]) / n for i in range(0, len(xs) - n + 1, n)]


def averaging_table(hwt: Column, scl: Column, rate: float) -> None:
    """Sigma against averaging window -- the only fair way to read the noise.

    Comparing raw sigma tells you which output is more filtered, not which
    sensor is better. Averaging separates the two: genuine white noise falls as
    1/sqrt(N), while an output that has already been smoothed to its floor
    barely moves however much you average it, because its samples are no longer
    independent. Read each row against the ideal beneath it.
    """
    windows = [n for n in (1, 2, 5, 10, 25, 50, 100) if len(hwt.acc) // n >= 8]
    if len(windows) < 2:
        return
    print("\n  Noise against averaging window -- sigma of AngX/AngY (deg)")
    print("    {:<16}".format("window (n)")
          + "".join("{:>9}".format(n) for n in windows))
    print("    {:<16}".format("bandwidth")
          + "".join("{:>9}".format("{:.2g}Hz".format(rate / n / 2)) for n in windows))
    print("    " + "-" * (16 + 9 * len(windows)))
    for col in (hwt, scl):
        for i, axis in enumerate("XY"):
            xs = [a[i] for a in col.ang]
            line = "    {:<16}".format("{} Ang{}".format(col.name.split("-")[0], axis))
            for n in windows:
                b = block_average(xs, n)
                line += "{:>9}".format("{:.4f}".format(stdev(b)) if len(b) > 1 else "-")
            print(line)
            ideal = stdev(xs)
            print("    {:<16}".format("  ideal 1/sqrtN")
                  + "".join("{:>9}".format("{:.4f}".format(ideal / math.sqrt(n)))
                            for n in windows))


def disturbances(hwt: Column, scl: Column, times: list[float]) -> None:
    """Samples where |acc| jumps, and whether both sensors saw the same one.

    A spike one sensor sees alone is that sensor's problem -- a dropped frame,
    a bad ground. A spike both see within the same moment is the bench being
    knocked, which is not a fault at all. Telling those apart by eye from a
    column of numbers is hopeless, so it is done here.
    """
    def spikes(col: Column) -> set[int]:
        m, sd = mean(col.magnitude), stdev(col.magnitude)
        if sd == 0.0:
            return set()
        return {i for i, v in enumerate(col.magnitude) if abs(v - m) > 5.0 * sd}

    a, b = spikes(hwt), spikes(scl)
    if not a and not b:
        return
    shared = sum(1 for i in b if any(abs(times[i] - times[j]) < 0.5 for j in a))
    print("\n  Disturbances -- |acc| beyond 5 sigma")
    print("    {:<26}{:>16}{:>16}".format("spikes", len(a), len(b)))
    print("    {} of the SCL3300's line up with one on the HWT9037".format(shared))

    # One sensor can only corroborate the other's spike if it can resolve one.
    # A device whose sigma sits well under its own quantisation step has been
    # smoothed past the point of reporting short events at all, so its silence
    # is not evidence -- reading it as evidence blames the wiring for a spike
    # that was really the floor.
    blind = [c for c in (hwt, scl)
             if stdev(c.magnitude) * 1000.0 < c.resolution_mg / 4.0]
    if blind:
        print("    {} cannot resolve one: its sigma is under a quarter of its own"
              .format(", ".join(c.name for c in blind)))
        print("    LSB, so it has nothing to say about the other's spikes.")
    elif shared >= max(1, len(b) // 2):
        print("    Both saw the same events, so this is the bench moving, not the bus.")
    elif b and not shared:
        print("    The SCL3300 spiked alone. Suspect the wiring before the bench.")


def report(hwt: Column, scl: Column, elapsed: float, rate: float,
           times: list[float]) -> None:
    w = "  {:<26}{:>16}{:>16}"
    print("\n" + "=" * 60)
    print("  {} samples in {:.1f} s ({:.1f} Hz)".format(len(hwt.acc), elapsed, rate))
    print("=" * 60)
    print(w.format("", hwt.name, scl.name))
    print("  " + "-" * 56)
    print(w.format("samples", len(hwt.acc), len(scl.acc)))
    print(w.format("dropped reads", hwt.misses, scl.misses))
    print(w.format("resolution (mg/LSB)", "{:.3f}".format(hwt.resolution_mg),
                   "{:.3f}".format(scl.resolution_mg)))

    print("\n  Absolute accuracy -- at rest both must read 1.000 g")
    print(w.format("|acc| mean (g)", "{:.4f}".format(mean(hwt.magnitude)),
                   "{:.4f}".format(mean(scl.magnitude))))
    print(w.format("error vs 1 g (%)",
                   "{:+.2f}".format((mean(hwt.magnitude) - 1.0) * 100.0),
                   "{:+.2f}".format((mean(scl.magnitude) - 1.0) * 100.0)))
    print(w.format("|acc| sigma (mg)", "{:.3f}".format(stdev(hwt.magnitude) * 1000.0),
                   "{:.3f}".format(stdev(scl.magnitude) * 1000.0)))

    print("\n  Noise -- sigma of each device's own angle output (deg)")
    for i, axis in enumerate("XYZ"):
        print(w.format("  Ang{} sigma".format(axis),
                       "{:.4f}".format(stdev([a[i] for a in hwt.ang])),
                       "{:.4f}".format(stdev([a[i] for a in scl.ang]))))
    for i, axis in enumerate("XYZ"):
        print(w.format("  Ang{} peak-to-peak".format(axis),
                       "{:.4f}".format(span([a[i] for a in hwt.ang])),
                       "{:.4f}".format(span([a[i] for a in scl.ang]))))

    if hwt.temp or scl.temp:
        print("\n  Temperature (C)")
        print(w.format("  mean", "{:.2f}".format(mean(hwt.temp)) if hwt.temp else "-",
                       "{:.2f}".format(mean(scl.temp)) if scl.temp else "-"))

    averaging_table(hwt, scl, rate)
    disturbances(hwt, scl, times)

    print("\n  Agreement -- tilt from the run's opening orientation")
    n = min(len(hwt.tilt), len(scl.tilt))
    if n == 0:
        print("    no reference established")
        return
    diff = [abs(hwt.tilt[i] - scl.tilt[i]) for i in range(n)]
    moved = max(max(hwt.tilt[:n]), max(scl.tilt[:n]))
    rms = math.sqrt(sum(d * d for d in diff) / n)
    print("    largest tilt seen      {:.3f} deg".format(moved))
    print("    RMS disagreement       {:.4f} deg".format(rms))
    print("    worst disagreement     {:.4f} deg".format(max(diff)))
    if moved < 1.0:
        # Without motion the two tilt tracks are both flat, so they agree
        # trivially. Saying so matters more than the number above.
        print("\n    The assembly barely moved, so this only compares noise.")
        print("    Tilt it through 10-20 deg during the run to test scale.")
    elif rms > 0.5:
        print("\n    They disagree by more than noise. Either the two are not")
        print("    rigid with respect to each other, or one of them is wrong.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare the HWT9037-485 against the SCL3300")
    port_config.add_port_argument(ap)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--rate", type=float, default=10.0, help="samples per second")
    ap.add_argument("--mode", type=int, default=4, choices=(1, 2, 3, 4),
                    help="SCL3300 measurement mode")
    ap.add_argument("--reference-samples", type=int, default=20,
                    help="opening samples averaged into the reference orientation")
    ap.add_argument("--csv", default="", help="write every sample here")
    ap.add_argument("--quiet", action="store_true", help="summary only")
    args = ap.parse_args()

    port = port_config.resolve_port(getattr(args, "port", None))
    device, baud = read_status.connectAutoBaud(port)
    if device is None:
        logger.error("HWT9037 did not answer on {}", port)
        logger.error("is the recorder still running? sudo systemctl stop verticrane-recorder")
        return 2
    device.verbose = False

    scl = scl3300.SCL3300(mode=args.mode - 1)
    try:
        scl.open()
        scl.start()
    except Exception as exc:                              # noqa: BLE001
        logger.error("SCL3300 did not start: {}", exc)
        device.closeDevice()
        return 2

    hwt_col = Column("HWT9037-485", _HWT_RESOLUTION_MG)
    scl_col = Column("SCL3300", _SCL_RESOLUTION_MG[args.mode - 1])

    writer = None
    handle = None
    if args.csv:
        handle = open(args.csv, "w", newline="")
        writer = csv.writer(handle)
        writer.writerow(["t", "hw_accx", "hw_accy", "hw_accz", "hw_angx", "hw_angy",
                         "hw_angz", "hw_temp", "scl_accx", "scl_accy", "scl_accz",
                         "scl_angx", "scl_angy", "scl_angz", "scl_temp"])

    period = 1.0 / args.rate
    started = time.monotonic()
    deadline = started + args.seconds
    next_at = started
    times: list[float] = []
    hw_temp: Optional[float] = None
    next_temp = 0.0

    if not args.quiet:
        print("\n  {:>7} {:>25} {:>25}".format("t(s)", "HWT9037 AngX/Y/Z",
                                               "SCL3300 AngX/Y/Z"))
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            # Temperature moves slowly and costs a whole Modbus round trip, so
            # it gets its own once-a-second cadence rather than the sample rate.
            if now >= next_temp:
                if device.readReg(*TEMP_REG) is not None:
                    hw_temp = device.get("Temp")
                next_temp = now + 1.0

            hw = read_hwt(device)
            if hw is None:
                hwt_col.misses += 1
            try:
                r = scl.read()
            except scl3300.SCL3300Error as exc:
                scl_col.misses += 1
                logger.error("SCL3300: {}", exc)
                r = None

            if hw is not None and r is not None:
                hwt_col.add(hw[0], hw[1], hw_temp)
                scl_col.add((r.acc_x, r.acc_y, r.acc_z),
                            (r.angle_x, r.angle_y, r.angle_z), r.temperature)
                t = now - started
                times.append(t)
                if writer is not None:
                    writer.writerow(["{:.4f}".format(t)]
                                    + ["{:.5f}".format(v) for v in hw[0]]
                                    + ["{:.4f}".format(v) for v in hw[1]]
                                    + ["{:.2f}".format(hw_temp) if hw_temp is not None else ""]
                                    + ["{:.5f}".format(v) for v in
                                       (r.acc_x, r.acc_y, r.acc_z)]
                                    + ["{:.4f}".format(v) for v in
                                       (r.angle_x, r.angle_y, r.angle_z)]
                                    + ["{:.2f}".format(r.temperature)])
                if not args.quiet:
                    print("  {:7.2f} {:8.3f}{:8.3f}{:9.3f} {:8.3f}{:8.3f}{:9.3f}".format(
                        t, hw[1][0], hw[1][1], hw[1][2], r.angle_x, r.angle_y, r.angle_z))

            next_at += period
            gap = next_at - time.monotonic()
            if gap > 0:
                time.sleep(gap)
            else:
                next_at = time.monotonic()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        if handle is not None:
            handle.close()
        scl.close()
        device.closeDevice()

    elapsed = time.monotonic() - started
    if not hwt_col.acc:
        logger.error("no paired samples were collected")
        return 1
    hwt_col.set_reference(args.reference_samples)
    scl_col.set_reference(args.reference_samples)
    hwt_col.compute_tilt()
    scl_col.compute_tilt()
    report(hwt_col, scl_col, elapsed,
           len(hwt_col.acc) / elapsed if elapsed else 0.0, times)
    if args.csv:
        print("\n  samples written to {}".format(args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
