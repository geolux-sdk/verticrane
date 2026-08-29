# coding:UTF-8
# Guided shake test: three captures, one per frequency, then the analysis.
#
#   python shake_run.py            slow / medium / fast, 20 s each
#   python shake_run.py --seconds 30
#   python shake_run.py --analyse-only     re-run the analysis on existing files
#
# This exists because the measurement it feeds needs a person. The HWT9037's
# angle output is heavily filtered and the SCL3300's is not, but a brief knock
# cannot show by how much: the knock left only two counts in the HWT9037, and a
# signal attenuated to its own quantisation step has no shape left to fit a time
# constant to.
#
# What does work is sustained motion at several frequencies, held long enough
# and hard enough that both sensors are well clear of their noise floors. Then
# the ratio of the two amplitudes at each frequency is a point on the HWT9037's
# frequency response: near 1 where it keeps up, climbing where it does not.
#
# Shake the assembly through several degrees, not a nudge. The HWT9037's LSB is
# 0.488 mg and anything under ten counts of that measures the quantisation grid
# rather than the response -- the analysis flags such a capture rather than
# reporting a number from it.

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMPARE = os.path.join(_HERE, "sensor_compare.py")

# Frequencies are chosen to straddle where the filter is expected to sit rather
# than to be exact -- the analysis reads the frequency actually achieved off the
# recording, so an approximate hand rhythm is enough.
PHASES = [
    ("slow", "느리게", "5초에 한 번 왕복  (약 0.2 Hz)"),
    ("mid", "보통", "1초에 한 번 왕복  (약 1 Hz)"),
    ("fast", "빠르게", "가능한 한 빠르게  (3 Hz 이상)"),
]

BAR = "=" * 62


def countdown(seconds: int, label: str, detail: str) -> None:
    print("\n" + BAR)
    print("  {}  —  {}".format(label, detail))
    print("  진폭은 몇 도 수준으로 크게. 살짝 건드리는 정도로는 측정이 안 됩니다.")
    print(BAR)
    for n in range(seconds, 0, -1):
        sys.stdout.write("\r  {}초 후 시작 — 준비하세요 ".format(n))
        sys.stdout.flush()
        time.sleep(1.0)
    sys.stdout.write("\r  지금 흔드세요!" + " " * 24 + "\n")
    sys.stdout.flush()


def capture(path: str, seconds: float, mode: int) -> bool:
    cmd = [sys.executable, _COMPARE, "--seconds", str(seconds), "--rate", "25",
           "--mode", str(mode), "--quiet", "--csv", path]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        print("  캡처 실패 ({}). 레코더가 아직 돌고 있는지 확인하세요:".format(path))
        print("    sudo systemctl stop verticrane-recorder")
        return False
    print("  기록 완료 → {}".format(path))
    return True


def analyse(paths: list[str]) -> int:
    print("\n" + BAR)
    print("  분석")
    print(BAR)
    return subprocess.run([sys.executable, _COMPARE, "--shake"] + paths).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="HWT9037의 주파수 응답 측정")
    ap.add_argument("--seconds", type=float, default=20.0, help="구간당 기록 시간")
    ap.add_argument("--lead", type=int, default=5, help="시작 전 카운트다운")
    ap.add_argument("--mode", type=int, default=3, choices=(1, 2, 3, 4),
                    help="SCL3300 측정 모드")
    ap.add_argument("--dir", default="/tmp", help="기록 파일을 둘 곳")
    ap.add_argument("--analyse-only", action="store_true",
                    help="이미 찍어둔 파일로 분석만 다시")
    args = ap.parse_args()

    paths = [os.path.join(args.dir, "shake_{}.csv".format(tag))
             for tag, _, _ in PHASES]

    if args.analyse_only:
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            print("파일이 없습니다: {}".format(", ".join(missing)))
            return 1
        return analyse(paths)

    print(BAR)
    print("  HWT9037 주파수 응답 측정 — 3회 × {:.0f}초".format(args.seconds))
    print(BAR)
    print("  두 센서가 붙은 판을 각 구간마다 지시된 속도로 계속 흔드세요.")
    print("  구간이 끝날 때까지 멈추지 말고, 속도를 일정하게 유지하면 좋습니다.")

    done: list[str] = []
    for (tag, label, detail), path in zip(PHASES, paths):
        countdown(args.lead, label, detail)
        if not capture(path, args.seconds, args.mode):
            return 1
        done.append(path)
        print("  멈추세요.")

    return analyse(done)


if __name__ == "__main__":
    sys.exit(main())
