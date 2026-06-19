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
    lines = (detail or "").split("\n")
    print("[{0}] {1} - {2}".format("PASS" if ok else "FAIL", name, lines[0]))
    for extra in lines[1:]:
        print("        {0}".format(extra))


def resultant_slope_pct(roll_deg, pitch_deg):
    # Same formula log_tilt.py records; the measurement path depends on it.
    sx = math.tan(math.radians(roll_deg))
    sy = math.tan(math.radians(pitch_deg))
    return math.hypot(sx, sy) * 100.0


def _stats(xs):
    # min, mean, max, std for a list (zeros when empty).
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    n = len(xs)
    m = sum(xs) / n
    sd = (sum((x - m) ** 2 for x in xs) / n) ** 0.5
    return min(xs), m, max(xs), sd


def check_config():
    cfg = app_config.load()
    ok = "slope_threshold_pct" in cfg and "ma_seconds" in cfg
    pin_state = ("기본 PIN 사용 중(변경 권장)" if app_config.verify_pin(cfg, app_config.DEFAULT_PIN)
                 else "PIN 변경됨")
    src = "config.json" if os.path.exists(app_config.CONFIG_PATH) else "기본값"
    return ok, "임계값 {0}% · 이동평균 {1}s · {2} · 출처 {3}".format(
        cfg.get("slope_threshold_pct"), cfg.get("ma_seconds"), pin_state, src)


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
    return ok, "합성 50샘플 분석 · 리포트 {0}자 (통계·FFT·평가 포함)".format(len(report))


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
        device.readReg(0x43, 1)  # temperature
        temp = device.deviceData.get("Temp")

        rolls, pitches, yaws, slopes = [], [], [], []
        total = 0
        start = time.perf_counter()
        next_tick = start
        while time.perf_counter() - start < seconds:
            device.readReg(0x34, 15)
            total += 1
            roll = device.deviceData.get("AngX")
            pitch = device.deviceData.get("AngY")
            yaw = device.deviceData.get("AngZ")
            if roll is not None and pitch is not None:
                rolls.append(roll)
                pitches.append(pitch)
                if yaw is not None:
                    yaws.append(yaw)
                slopes.append(resultant_slope_pct(roll, pitch))
            next_tick += SAMPLE_PERIOD_S
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)

        elapsed = time.perf_counter() - start
        good = len(slopes)
        rate = good / elapsed if elapsed > 0 else 0.0
        ok = good >= 1 and good >= total * 0.5 and vals.get("fw_version") not in (None, "-")

        s_min, s_avg, s_max, s_sd = _stats(slopes)
        r_min, r_avg, r_max, _ = _stats(rolls)
        p_min, p_avg, p_max, _ = _stats(pitches)
        yaw_last = yaws[-1] if yaws else None
        dd = device.deviceData
        detail = "\n".join([
            "{0} bps · fw {1} · S/N {2} · Modbus {3}".format(
                baud, vals.get("fw_version"), vals.get("serial_number"), vals.get("modbus_addr")),
            "대역폭 {0} · 자이로 {1} · 가속 {2} · {3} · {4}".format(
                vals.get("bandwidth"), vals.get("gyro_range"), vals.get("acc_range"),
                vals.get("algorithm"), vals.get("installation")),
            "필터 K={0} · Acc={1} · RS485지연 {2} · 온도 {3} °C".format(
                vals.get("filter_k"), vals.get("acc_filter"), vals.get("rs485_delay"), temp),
            "라이브 {0:.1f}s · {1}/{2} 샘플 ({3:.1f} Hz)".format(elapsed, good, total, rate),
            "Roll {0:.3f}~{1:.3f} (평균 {2:.3f}) · Pitch {3:.3f}~{4:.3f} (평균 {5:.3f}) deg".format(
                r_min, r_max, r_avg, p_min, p_max, p_avg),
            "Yaw {0} deg · slope min/avg/max/std = {1:.4f}/{2:.4f}/{3:.4f}/{4:.4f} %".format(
                "{0:.3f}".format(yaw_last) if yaw_last is not None else "-", s_min, s_avg, s_max, s_sd),
            "Acc(g) X={0} Y={1} Z={2} · Gyro(dps) X={3} Y={4} Z={5}".format(
                dd.get("AccX"), dd.get("AccY"), dd.get("AccZ"),
                dd.get("AsX"), dd.get("AsY"), dd.get("AsZ")),
        ])
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
