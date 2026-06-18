# coding:UTF-8
# Log HWT9037-485 tilt data to CSV for a fixed duration (default 10 minutes).
#
#   python log_tilt.py [minutes]
#
# Records at ~25 Hz: timestamp, Roll/Pitch/Yaw, resultant slope (%), accel, gyro, temp.
# The sensor bandwidth is 10 Hz, so sampling must exceed 2*10 = 20 Hz (Nyquist) to
# avoid aliasing high-frequency vibration into the slow tilt band. 25 Hz adds a guard band.
# Analysis is done separately/later -- this script only captures raw measurements.

import csv
import math
import os
import sys
import time

import analyze_tilt
from hwt9037_485 import HWT9037_485


# All measurements (CSV + matching analysis .txt) are stored here.
OUTPUT_DIR = "data"


PORT_NAME = "COM11"
DEVICE_ADDR = 0x50
# The device powers on at 9600 bps but may be saved to 115200; probe both.
CANDIDATE_BAUDS = [9600, 115200]

# Sensor bandwidth is 10 Hz; sample above 2x that (Nyquist) plus a guard band.
SAMPLE_RATE_HZ = 25.0
# Temperature drifts slowly, so refresh it once per second rather than every cycle.
TEMP_REFRESH_S = 1.0
DEFAULT_MINUTES = 10.0
PROGRESS_EVERY_S = 30.0


def connectAutoBaud():
    for baud in CANDIDATE_BAUDS:
        device = HWT9037_485(PORT_NAME, baud, DEVICE_ADDR, lambda d: None)
        device.openDevice()
        if not device.isOpen:
            continue
        device.readReg(0x2E, 1)  # VERSION confirms the link
        if device.registerData.get(0x2E) is not None:
            print("Connected at {0} bps".format(baud))
            return device, baud
        print("No response at {0} bps".format(baud))
        device.closeDevice()
    return None, None


def resultant_slope_pct(roll_deg, pitch_deg):
    # Combined tilt as a slope percentage, direction-independent.
    if roll_deg is None or pitch_deg is None:
        return None
    sx = math.tan(math.radians(roll_deg))
    sy = math.tan(math.radians(pitch_deg))
    return math.hypot(sx, sy) * 100.0


def main():
    minutes = DEFAULT_MINUTES
    if len(sys.argv) > 1:
        minutes = float(sys.argv[1])
    duration_s = minutes * 60.0
    period = 1.0 / SAMPLE_RATE_HZ

    device, _ = connectAutoBaud()
    if device is None:
        print("Could not reach the device on {0}".format(PORT_NAME))
        return
    # Silence per-transaction Modbus prints during high-rate logging.
    device.verbose = False

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = os.path.join(OUTPUT_DIR, "tilt_log_{0}.csv".format(time.strftime("%Y%m%d_%H%M%S")))
    print("Logging {0:.1f} min at {1:.0f} Hz -> {2}".format(minutes, SAMPLE_RATE_HZ, filename))
    print("Press Ctrl+C to stop early.")

    columns = [
        "timestamp", "elapsed_s",
        "Roll_deg", "Pitch_deg", "Yaw_deg", "slope_pct",
        "AccX_g", "AccY_g", "AccZ_g",
        "GyroX_dps", "GyroY_dps", "GyroZ_dps",
        "Temp_C",
    ]

    samples = 0
    start = time.perf_counter()
    next_tick = start
    next_progress = PROGRESS_EVERY_S
    next_temp = 0.0

    try:
        with open(filename, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)

            while True:
                elapsed = time.perf_counter() - start
                if elapsed >= duration_s:
                    break

                # One angle block (Acc+Gyro+Mag+Angle) every cycle keeps the rate high.
                device.readReg(0x34, 15)
                # Temperature changes slowly; refresh it about once per second.
                if elapsed >= next_temp:
                    device.readReg(0x43, 1)
                    next_temp += TEMP_REFRESH_S
                data = device.deviceData

                roll = data.get("AngX")
                pitch = data.get("AngY")
                yaw = data.get("AngZ")
                slope = resultant_slope_pct(roll, pitch)

                writer.writerow([
                    time.strftime("%Y-%m-%d %H:%M:%S"), round(elapsed, 3),
                    roll, pitch, yaw,
                    round(slope, 4) if slope is not None else None,
                    data.get("AccX"), data.get("AccY"), data.get("AccZ"),
                    data.get("AsX"), data.get("AsY"), data.get("AsZ"),
                    data.get("Temp"),
                ])
                samples += 1

                if elapsed >= next_progress:
                    f.flush()
                    print("  t={0:6.1f}s  n={1:5d}  Roll={2}  Pitch={3}  slope={4}%".format(
                        elapsed, samples, roll, pitch,
                        round(slope, 4) if slope is not None else "-"))
                    next_progress += PROGRESS_EVERY_S

                # Pace to a steady sample rate.
                next_tick += period
                sleep_for = next_tick - time.perf_counter()
                if sleep_for > 0:
                    time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("Stopped early by user.")
    finally:
        device.closeDevice()

    elapsed = time.perf_counter() - start
    rate = samples / elapsed if elapsed > 0 else 0.0
    print("Done: {0} samples in {1:.1f} s ({2:.1f} Hz) -> {3}".format(
        samples, elapsed, rate, filename))

    # Write an analysis report alongside the CSV (same base name, .txt).
    if samples > 0:
        report = analyze_tilt.analyze(filename)
        txt_path = os.path.splitext(filename)[0] + ".txt"
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print("Analysis report -> {0}".format(txt_path))


if __name__ == "__main__":
    main()
