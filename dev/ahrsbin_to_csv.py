# coding:UTF-8
# Convert a .ahrsbin recording to the CSV the existing analysis tools read.
#
#   python dev/ahrsbin_to_csv.py data/TOP_20260827_143629.ahrsbin
#   python dev/ahrsbin_to_csv.py data/*.ahrsbin --report
#
# The columns match what log_tilt.py has always written, so analyze_tilt.py and
# the dashboard work on new recordings without changes. slope_pct is computed
# from Roll/Pitch rather than read: it is not stored, precisely so it cannot
# disagree with the angles it comes from.

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
import time

# dev/ tools live one level down, so put the repo root on the import path
# before reaching for app_config, ahrs_file and the rest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

import ahrs_file as af

COLUMNS: tuple[str, ...] = (
    "timestamp", "elapsed_s",
    "Roll_deg", "Pitch_deg", "Yaw_deg", "slope_pct",
    "AccX_g", "AccY_g", "AccZ_g",
    "GyroX_dps", "GyroY_dps", "GyroZ_dps",
    "MagX", "MagY", "MagZ",
    "Temp_C",
)


def convert(path: str, out_path: str) -> int:
    header: af.Header = af.read_header(path)
    # What the device believed when it opened the file, which is all anyone
    # knows (section 5.2). The elapsed times added to it are exact.
    start_epoch: float = header.start_epoch

    rows: int = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for block in af.iter_blocks(path):
            times = block.sample_times_ms(header.sample_rate_hz)
            for sample, t_ms in zip(block.samples, times):
                elapsed: float = t_ms / 1000.0
                writer.writerow([
                    time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(start_epoch + elapsed)),
                    round(elapsed, 3),
                    round(sample.roll, 4), round(sample.pitch, 4), round(sample.yaw, 4),
                    round(sample.tilt_pct, 6),
                    round(sample.acc[0], 5), round(sample.acc[1], 5), round(sample.acc[2], 5),
                    round(sample.gyro[0], 4), round(sample.gyro[1], 4), round(sample.gyro[2], 4),
                    round(sample.mag[0], 2), round(sample.mag[1], 2), round(sample.mag[2], 2),
                    # One temperature covers the whole block, so it repeats across
                    # its 25 rows. The sensor only updates it once a second.
                    round(block.temp_c, 2),
                ])
                rows += 1
    return rows


def describe(path: str) -> None:
    header: af.Header = af.read_header(path)
    summary: af.Summary = af.scan(path)
    start: float = header.start_epoch
    print("  {0}".format(os.path.basename(path)))
    print("    센서      : {0} [{1}]  {2}".format(
        header.sensor_id or "-", header.position_name, header.device_serial or "-"))
    print("    시작      : {0}{1}".format(
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)),
        "  (전원 차단 복구)" if header.recovered else ""))
    print("    블록/샘플 : {0} / {1}   {2:.1f} s @ {3} Hz".format(
        summary.blocks, summary.samples, summary.duration_s, header.sample_rate_hz))
    flagged: int = sum(1 for b in af.iter_blocks(path) if b.flags)
    if flagged:
        print("    상태 플래그가 붙은 블록: {0}".format(flagged))
    if summary.truncated:
        print("    ** 끝이 잘려 있습니다 (복구되지 않은 파일) **")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert .ahrsbin recordings to CSV for the analysis tools.")
    parser.add_argument("files", nargs="+", help="One or more .ahrsbin files (globs allowed).")
    parser.add_argument("--out-dir", help="Where to write the CSVs (default: beside the input).")
    parser.add_argument("--report", action="store_true",
                        help="Also run analyze_tilt on each converted file.")
    args = parser.parse_args()

    paths: list[str] = []
    for pattern in args.files:
        paths.extend(sorted(glob.glob(pattern)) or [pattern])

    failures: int = 0
    for path in paths:
        if not os.path.exists(path):
            logger.error("No such file: {}", path)
            failures += 1
            continue
        try:
            describe(path)
        except (OSError, af.FormatError) as exc:
            logger.error("{}: {}", os.path.basename(path), exc)
            failures += 1
            continue

        base: str = os.path.splitext(os.path.basename(path))[0]
        out_dir: str = args.out_dir or os.path.dirname(os.path.abspath(path))
        os.makedirs(out_dir, exist_ok=True)
        out_path: str = os.path.join(out_dir, base + ".csv")
        try:
            rows: int = convert(path, out_path)
        except (OSError, af.FormatError) as exc:
            logger.error("{}: {}", os.path.basename(path), exc)
            failures += 1
            continue
        print("    -> {0}  ({1} rows)".format(out_path, rows))

        if args.report:
            import analyze_tilt
            report: str = analyze_tilt.analyze(out_path)
            txt_path: str = os.path.splitext(out_path)[0] + ".txt"
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(report + "\n")
            print("    -> {0}".format(txt_path))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
