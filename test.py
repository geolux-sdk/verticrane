# coding:UTF-8
# Self-test: exercise the building blocks the dashboard relies on and report
# pass/fail for each, so a fresh install (e.g. on the Raspberry Pi) can be verified.
#
#   python test.py                  # run all checks (sensor must be connected)
#   python test.py --port /dev/ttyUSB0
#   python test.py --no-hardware    # software-only checks (no sensor needed)
#
# Checks: port resolution (port_config), settings (app_config), CSV analysis
# (analyze_tilt), logger module import (log_tilt), and the device link + a few
# live samples (read_status / the measurement path). The sensor is required for
# the last one unless --no-hardware is given.

import argparse
import math
import os
import sys
import tempfile
import time

import analyze_tilt
import app_config
import read_status
from port_config import add_port_argument, resolve_port

# Touchstone columns written by log_tilt.py (used by the synthetic analysis check).
CSV_COLUMNS = ("timestamp,elapsed_s,Roll_deg,Pitch_deg,Yaw_deg,slope_pct,"
               "AccX_g,AccY_g,AccZ_g,GyroX_dps,GyroY_dps,GyroZ_dps,Temp_C")

# Live-sample rate for the hardware check, matching log_tilt's 25 Hz logging.
SAMPLE_PERIOD_S = 0.04

_results = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as ex:  # a check should never crash the whole run
        ok, detail = False, "예외: {0}".format(ex)
    _results.append(ok)
    print("[{0}] {1} - {2}".format("PASS" if ok else "FAIL", name, detail))


def resultant_slope_pct(roll_deg, pitch_deg):
    # Same formula log_tilt.py records; the measurement path depends on it.
    sx = math.tan(math.radians(roll_deg))
    sy = math.tan(math.radians(pitch_deg))
    return math.hypot(sx, sy) * 100.0


def check_config():
    cfg = app_config.load()
    ok = "slope_threshold_pct" in cfg and "ma_seconds" in cfg
    return ok, "임계값 {0}% · 이동평균 {1}s".format(
        cfg.get("slope_threshold_pct"), cfg.get("ma_seconds"))


def check_analyze():
    # Write a small synthetic log and confirm analyze() returns a real report.
    path = os.path.join(tempfile.gettempdir(), "verticrane_selftest.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(CSV_COLUMNS + "\n")
        for i in range(50):
            f.write("2026-01-01 00:00:00,{0:.2f},0.30,-0.20,10.0,0.50,"
                    "0.0,0.0,1.0,0,0,0,25.0\n".format(i * 0.04))
    report = analyze_tilt.analyze(path)
    os.remove(path)
    ok = "샘플 수" in report and "찾을 수 없습니다" not in report
    return ok, "리포트 {0}자 생성".format(len(report))


def check_logger_module():
    # The dashboard launches log_tilt.py as a subprocess; confirm it imports
    # (which also loads hwt9037_485, port_config, analyze_tilt).
    import log_tilt
    ok = hasattr(log_tilt, "resultant_slope_pct") and hasattr(log_tilt, "main")
    return ok, "log_tilt 및 의존 모듈 로드 OK"


def check_hardware(port, seconds):
    # One connection: decode the config (read_status) and read live samples for the
    # requested duration (the measurement path), so a single auto-baud probe covers both.
    device, baud = read_status.connectAutoBaud(port)
    if device is None:
        return False, "장치 무응답 ({0})".format(port)
    try:
        device.verbose = False
        read_status.read_config_registers(device)
        vals = read_status.decode_status(device)

        good = 0
        total = 0
        last_slope = None
        start = time.perf_counter()
        next_tick = start
        while time.perf_counter() - start < seconds:
            device.readReg(0x34, 15)
            total += 1
            roll = device.deviceData.get("AngX")
            pitch = device.deviceData.get("AngY")
            if roll is not None and pitch is not None:
                good += 1
                last_slope = resultant_slope_pct(roll, pitch)
            next_tick += SAMPLE_PERIOD_S
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)

        elapsed = time.perf_counter() - start
        rate = good / elapsed if elapsed > 0 else 0.0
        ok = good >= 1 and good >= total * 0.5 and vals.get("fw_version") not in (None, "-")
        detail = "{0} bps · fw {1} · {2} · {3:.1f}s 동안 {4}/{5} 샘플 ({6:.1f} Hz) · slope {7}".format(
            baud, vals.get("fw_version"), vals.get("algorithm"), elapsed, good, total, rate,
            "{0:.4f}%".format(last_slope) if last_slope is not None else "-")
        return ok, detail
    finally:
        device.closeDevice()


def main():
    parser = argparse.ArgumentParser(
        description="Verify the building blocks the dashboard relies on.")
    add_port_argument(parser)
    parser.add_argument("--no-hardware", action="store_true",
                        help="Skip checks that need the sensor connected.")
    parser.add_argument("--seconds", type=float, default=1.0,
                        help="Live-sample duration for the hardware check (seconds, default 1.0).")
    args = parser.parse_args()
    port = resolve_port(args.port)

    print("=" * 56)
    print("Verticrane 대시보드 기능 자가 점검")
    print("=" * 56)

    check("포트 결정 (port_config)", lambda: (bool(port), "port = {0}".format(port)))
    check("설정 로드 (app_config)", check_config)
    check("CSV 분석 (analyze_tilt)", check_analyze)
    check("로거 모듈 (log_tilt)", check_logger_module)
    if args.no_hardware:
        print("[SKIP] 센서 통신 + 라이브 (--no-hardware)")
    else:
        check("센서 통신 + 라이브 (read_status)", lambda: check_hardware(port, args.seconds))

    passed = sum(1 for ok in _results if ok)
    total = len(_results)
    print("-" * 56)
    print("{0}/{1} 통과".format(passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
