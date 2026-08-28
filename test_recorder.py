# coding:UTF-8
# Self-check for the recording loop's failure paths (sections 3, 6).
#
#   python test_recorder.py
#
# No hardware and no sensor: a Recorder is built with a port that will never
# open, which is deliberate -- the device has to keep going when the sensor is
# absent, so constructing one without it is a supported state.

from __future__ import annotations

import os
import sys
import tempfile
import shutil

from loguru import logger

import ahrs_file as af
import app_config
import recorder

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", label,
                                 "" if ok else "  <- " + detail))
    if not ok:
        _failures.append(label)


class _RefusingWriter:
    """A card that has gone read-only, or filled up, mid-recording."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.writes = 0
        self.closed = False

    def write_block(self, block: af.Block) -> None:
        self.writes += 1
        raise OSError(28, "No space left on device")

    def close(self) -> None:
        self.closed = True


def _full_block() -> list:
    sample = af.Sample(acc=(0.0, 0.0, 1.0), gyro=(0.0, 0.0, 0.0),
                       mag=(0.0, 0.0, 0.0), roll=0.0, pitch=0.0, yaw=0.0)
    return [(i * 40.0, sample) for i in range(af.SAMPLES_PER_BLOCK)]


def test_write_failure(tmp: str) -> None:
    """A refused write must stop the recording, not the process.

    The stop flushes what is pending, and what is pending is the block that was
    just refused. Leaving it there sent the same bytes back to the same card
    until the recursion took the process down -- and a device that quietly
    stopped recording is the worst outcome this project has.
    """
    print("\n[1] 쓰기 실패")
    rec = recorder.Recorder("/dev/null", app_config.RECORDER_DEFAULTS.copy(), tmp)
    writer = _RefusingWriter(os.path.join(tmp, "TOP_000.dat.partial"))
    rec.writer = writer                       # type: ignore[assignment]
    rec._record_start_mono = 0.0
    rec._pending = _full_block()

    try:
        rec._flush_block()
        survived = True
    except RecursionError:
        survived = False
    check("프로세스가 살아남는다 (재귀 없음)", survived)
    if not survived:
        return

    check("한 번만 시도한다", writer.writes == 1, str(writer.writes))
    check("writer 를 닫는다", writer.closed)
    check("기록을 멈춘다", rec.writer is None)
    check("쓰지 못한 샘플을 버린다", not rec._pending, str(len(rec._pending)))
    check("오류를 상태에 남긴다", "No space left" in (rec.status.error or ""),
          repr(rec.status.error))

    # Whatever arrives next must not walk back into the same path.
    rec._pending = _full_block()
    try:
        rec._flush_block()
        check("멈춘 뒤에 들어온 샘플도 안전하다", True)
    except (RecursionError, AttributeError) as exc:
        check("멈춘 뒤에 들어온 샘플도 안전하다", False, repr(exc))


def test_short_block_is_dropped(tmp: str) -> None:
    """A block that never filled is dropped, not padded (section 5.2)."""
    print("\n[2] 덜 찬 블록")
    rec = recorder.Recorder("/dev/null", app_config.RECORDER_DEFAULTS.copy(), tmp)
    writer = _RefusingWriter(os.path.join(tmp, "TOP_001.dat.partial"))
    rec.writer = writer                       # type: ignore[assignment]
    rec._record_start_mono = 0.0
    rec._pending = _full_block()[:5]

    rec._flush_block()
    check("쓰지 않는다", writer.writes == 0, str(writer.writes))
    check("기록은 계속된다", rec.writer is not None)
    check("모아둔 것은 비운다", not rec._pending)


def main() -> int:
    logger.remove()
    logger.add(sys.stderr, level="ERROR")
    print("=" * 56)
    print("기록 루프 자가 점검")
    print("=" * 56)
    tmp: str = tempfile.mkdtemp(prefix="rec_test_")
    try:
        test_write_failure(tmp)
        test_short_block_is_dropped(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n{0} 실패".format(len(_failures)) if _failures else "\n전부 통과")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
