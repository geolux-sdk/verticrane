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
from test_ahrs_file import make_blocks

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


class _UnopenableWriter:
    """Stands in for af.Writer on a card that will not take a new file."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise OSError(30, "Read-only file system")


def test_open_failure_gives_the_slot_back(tmp: str) -> None:
    """A card that refuses every open must not walk through the slot numbers.

    The slot is claimed before the file is opened -- it has to be, or boot
    recovery's leftover would be handed its own number again -- so a failed
    open has to give it back. Otherwise a quarter of an hour of retries burns
    a thousand slots, writing to the failing card each time to do it.
    """
    print("\n[3] 열기 실패")
    room: str = os.path.join(tmp, "unopenable")
    os.makedirs(room, exist_ok=True)
    rec = recorder.Recorder("/dev/null", app_config.RECORDER_DEFAULTS.copy(), room)
    rec.state = recorder.WAITING_STABLE

    original = af.Writer
    af.Writer = _UnopenableWriter                 # type: ignore[assignment]
    try:
        for _ in range(recorder.OPEN_FAILURE_LIMIT - 1):
            rec._start_recording()
        check("슬롯이 제자리에 있다", af.next_slot(room) == 0,
              "counter moved after failures")
        af.release_slot(room, 0)

        check("아직 포기하지 않는다", rec.state == recorder.WAITING_STABLE,
              rec.state)
        rec._start_recording()
        check("연속 한계를 넘으면 멈춘다", rec.state == recorder.MAINTENANCE,
              rec.state)
        check("오류를 남긴다", "Read-only" in (rec.status.error or ""),
              repr(rec.status.error))
    finally:
        af.Writer = original                      # type: ignore[assignment]

    # A card that comes back must clear the count, not stay one failure from
    # giving up for the rest of the boot.
    rec.state = recorder.WAITING_STABLE
    rec._start_recording()
    check("복구되면 실패 횟수가 초기화된다", rec._open_failures == 0,
          str(rec._open_failures))
    check("기록이 시작된다", rec.state == recorder.RECORDING, rec.state)
    rec._stop_recording()


def test_slot_is_never_appended_to(tmp: str) -> None:
    """Reusing a slot truncates. It must never continue an older file.

    Appending would put two recordings under one header, and every block CRC
    would still pass -- nothing downstream could tell.
    """
    print("\n[4] 슬롯 재사용")
    room: str = os.path.join(tmp, "reuse")
    os.makedirs(room, exist_ok=True)
    path: str = os.path.join(room, "TOP_000.dat.partial")

    header = af.Header(start_epoch=1000.0, position=af.POS_TOP, sensor_id="first")
    with af.Writer(path, header) as w:
        for block in make_blocks(4):
            w.write_block(block)
    first: int = os.path.getsize(path)

    header2 = af.Header(start_epoch=2000.0, position=af.POS_TOP, sensor_id="second")
    with af.Writer(path, header2) as w:
        for block in make_blocks(2):
            w.write_block(block)

    check("이어붙지 않는다", os.path.getsize(path) < first,
          "{0} vs {1}".format(os.path.getsize(path), first))
    check("두 번째 헤더가 남는다", af.read_header(path).sensor_id == "second")
    check("두 번째 블록 수만 있다", af.scan(path).blocks == 2,
          str(af.scan(path).blocks))


def test_snapshot_is_a_copy(tmp: str) -> None:
    """What the web and the panel read must not change under them."""
    print("\n[5] 스냅샷")
    rec = recorder.Recorder("/dev/null", app_config.RECORDER_DEFAULTS.copy(), tmp)
    rec.status.samples = 100
    snap = rec.snapshot()
    rec.status.samples = 999
    check("복사본이다", snap is not rec.status)
    check("찍힌 순간의 값을 유지한다", snap.samples == 100, str(snap.samples))


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
        test_open_failure_gives_the_slot_back(tmp)
        test_slot_is_never_appended_to(tmp)
        test_snapshot_is_a_copy(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n{0} 실패".format(len(_failures)) if _failures else "\n전부 통과")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
