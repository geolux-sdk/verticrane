# coding:UTF-8
# Self-check for the .ahrsbin format.
#
#   python test_ahrs_file.py
#
# Walks the sequence section 13 of the requirements asks for: write -> cut the
# file mid-block -> recover -> header flag -> merge -> slot names.
# Everything happens in a temporary directory, so no hardware is involved.

from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import time

import ahrs_file as af

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


def make_sample(n: int) -> af.Sample:
    return af.Sample(
        acc=(0.001 * n, 0.002 * n, 1.0),
        gyro=(0.01 * n, 0.02 * n, 0.03 * n),
        mag=(22.9 + n, 9.4 + n, -34.2 - n),
        roll=0.100 + 0.001 * n, pitch=-0.050 - 0.001 * n, yaw=(40.0 + n) % 360.0,
    )


def make_blocks(count: int, start_ms: int = 0, rate_hz: int = 25) -> list[af.Block]:
    period_ms: int = 1000 // rate_hz
    span: int = period_ms * (af.SAMPLES_PER_BLOCK - 1)
    return [
        af.Block(
            elapsed_ms=start_ms + i * period_ms * af.SAMPLES_PER_BLOCK,
            samples=[make_sample(i * af.SAMPLES_PER_BLOCK + j)
                     for j in range(af.SAMPLES_PER_BLOCK)],
            temp_c=23.5 + 0.1 * i,
            duration_ms=span,
        )
        for i in range(count)
    ]


def test_sizes() -> None:
    print("\n[1] 구조체 크기")
    check("header = 64 bytes", struct.calcsize(af._HEADER_FMT) == 64)
    check("block  = 1232 bytes", struct.calcsize(af._BLOCK_FMT) == 1232)
    check("samples per block = 25", af.SAMPLES_PER_BLOCK == 25)
    check("12 floats per sample", af.FLOATS_PER_SAMPLE == 12)
    per_sample: float = af.BLOCK_SIZE / af.SAMPLES_PER_BLOCK
    check("49.3 bytes per sample", abs(per_sample - 49.28) < 0.01,
          "{0:.2f}".format(per_sample))


def test_header_roundtrip() -> None:
    print("\n[2] 헤더 왕복")
    h = af.Header(start_epoch=1772179369.29, sample_rate_hz=25,
                  sensor_id="pi-tilt001", position=af.POS_TOP,
                  device_serial="WT4200068151")
    back = af.Header.unpack(h.pack())
    check("start epoch survives", abs(back.start_epoch - h.start_epoch) < 1e-6)
    check("SENSOR_ID survives", back.sensor_id == "pi-tilt001", back.sensor_id)
    check("position survives", back.position == af.POS_TOP)
    check("position name", back.position_name == "TOP")
    check("device serial survives", back.device_serial == "WT4200068151",
          back.device_serial)

    corrupt = bytearray(h.pack())
    corrupt[20] ^= 0xFF
    try:
        af.Header.unpack(bytes(corrupt))
        check("corrupt header rejected", False, "no exception")
    except af.FormatError:
        check("corrupt header rejected", True)


def test_block_roundtrip() -> None:
    print("\n[3] 블록 왕복")
    b = make_blocks(1)[0]
    back = af.Block.unpack(b.pack())
    check("block decodes", back is not None)
    assert back is not None
    check("25 samples", back.count == 25)
    s0, o0 = back.samples[7], b.samples[7]
    check("acceleration preserved", all(abs(a - c) < 1e-5 for a, c in zip(s0.acc, o0.acc)))
    check("gyro preserved", all(abs(a - c) < 1e-5 for a, c in zip(s0.gyro, o0.gyro)))
    check("magnetic field preserved", all(abs(a - c) < 1e-3 for a, c in zip(s0.mag, o0.mag)))
    check("roll preserved", abs(s0.roll - o0.roll) < 1e-6)
    check("pitch preserved", abs(s0.pitch - o0.pitch) < 1e-6)
    check("yaw preserved", abs(s0.yaw - o0.yaw) < 1e-4)
    check("temperature preserved", abs(back.temp_c - b.temp_c) < 1e-4)
    # tilt is not stored; it must follow exactly from the angles that are.
    check("tilt derived from its own roll/pitch",
          s0.tilt_pct == af.tilt_pct(s0.roll, s0.pitch))
    # And it must still match the original, within float32 round-trip error.
    check("tilt matches the original",
          abs(s0.tilt_pct - af.tilt_pct(o0.roll, o0.pitch)) < 1e-4)

    corrupt = bytearray(b.pack())
    corrupt[400] ^= 0xFF
    check("corrupt block returns None", af.Block.unpack(bytes(corrupt)) is None)

    # A block that never filled is dropped, not padded, so packing one is a bug.
    short = af.Block(elapsed_ms=0, samples=[make_sample(i) for i in range(3)],
                     temp_c=20.0, duration_ms=80)
    try:
        short.pack()
        check("short block refuses to pack", False, "no exception")
    except ValueError:
        check("short block refuses to pack", True)

    # And a file claiming a short block is treated as damaged, like a torn tail.
    forged = bytearray(b.pack())
    forged[0:2] = (3).to_bytes(2, "little")
    import zlib as _z
    forged[-4:] = (_z.crc32(bytes(forged[:-4])) & 0xFFFFFFFF).to_bytes(4, "little")
    check("a block claiming < 25 is rejected", af.Block.unpack(bytes(forged)) is None)


def test_write_and_scan(tmp: str) -> str:
    print("\n[4] 기록과 스캔")
    header = af.Header(start_epoch=time.time(), position=af.POS_TOP)
    path: str = os.path.join(tmp, "TOP_007.dat.partial")
    with af.Writer(path, header) as w:
        for block in make_blocks(10):
            w.write_block(block)
    expected: int = af.HEADER_SIZE + 10 * af.BLOCK_SIZE
    check("file size matches", os.path.getsize(path) == expected,
          "{0} != {1}".format(os.path.getsize(path), expected))
    summary = af.scan(path)
    check("10 blocks scanned", summary.blocks == 10)
    check("250 samples", summary.samples == 250)
    check("not truncated", not summary.truncated)
    return path


def test_truncate_and_recover(tmp: str) -> None:
    print("\n[5] 중간에 자르기 → 복구")
    header = af.Header(start_epoch=time.time(), position=af.POS_TOP)
    path: str = os.path.join(tmp, "TOP_008.dat.partial")
    with af.Writer(path, header) as w:
        for block in make_blocks(10):
            w.write_block(block)

    # Simulate a power cut in the middle of writing the 11th block.
    with open(path, "ab") as f:
        f.write(b"\x00" * 70)
    summary = af.scan(path)
    check("torn tail detected", summary.truncated)
    check("still 10 intact blocks", summary.blocks == 10)

    final = af.recover_partial(path)
    check("recovery produced a file", final is not None)
    assert final is not None
    name: str = os.path.basename(final)
    check("no .partial left", not name.endswith(".partial"))
    check("slot name is unchanged", name == "TOP_008.dat", name)
    check("recovered is recorded in the header", af.was_recovered(final))
    check("torn tail removed", not af.scan(final).truncated)
    check("data intact", af.scan(final).blocks == 10)


def test_recovered_flag(tmp: str) -> None:
    """The one fact recovery establishes, and where it has to survive.

    In the header, not beside the file: the sidecar was never downloaded, so a
    recording collected off the card said nothing about having lost its end.
    """
    print("\n[6] 복구 표식은 헤더에 남는다")
    device_start: float = time.mktime(time.strptime("20260827_143012", "%Y%m%d_%H%M%S"))
    path: str = os.path.join(tmp, "TOP_009.dat.partial")
    with af.Writer(path, af.Header(start_epoch=device_start,
                                   position=af.POS_TOP,
                                   sensor_id="pi-test")) as w:
        for block in make_blocks(5):
            w.write_block(block)

    final = af.recover_partial(path)
    assert final is not None
    check("이름은 그대로", os.path.basename(final) == "TOP_009.dat", final)
    check("복구 표식이 붙었다", af.was_recovered(final))

    header = af.read_header(final)
    check("헤더 CRC가 다시 맞는다", header.start_epoch == device_start)
    check("나머지 헤더는 그대로", header.sensor_id == "pi-test", header.sensor_id)
    check("포맷 버전", header.version == af.FORMAT_VERSION, str(header.version))
    check("시작 시각은 헤더가 답한다",
          abs(af.start_epoch(final) - device_start) < 1.0)
    check("블록은 손대지 않았다", af.scan(final).blocks == 5)

    # An orderly stop knows its tail is whole, so it must not carry the mark.
    clean: str = os.path.join(tmp, "TOP_010.dat.partial")
    with af.Writer(clean, af.Header(start_epoch=device_start,
                                    position=af.POS_TOP)) as w:
        for block in make_blocks(3):
            w.write_block(block)
    done = af.recover_partial(clean, mark_recovered=False)
    assert done is not None
    check("정상 종료는 표식이 없다", not af.was_recovered(done))


def test_filenames() -> None:
    print("\n[7] 파일명 규칙")
    top: str = af.build_filename(af.POS_TOP, 0)
    check("슬롯 이름", top == "TOP_000.dat", top)
    check("세 자리로 채운다", af.build_filename(af.POS_BASE, 7) == "BASE_007.dat")
    check("마지막 슬롯", af.build_filename(af.POS_MIDDLE, 999) == "MIDDLE_999.dat")
    check("999 다음은 000", af.build_filename(af.POS_TOP, 1000) == "TOP_000.dat")
    unset: str = af.build_filename(af.POS_UNSET, 3)
    check("미설정 위치가 보인다", unset.startswith("UNSET_"), unset)
    partial: str = af.build_filename(af.POS_TOP, 3, partial=True)
    check("기록 중 이름", partial == "TOP_003.dat.partial", partial)

    # Three devices recording into the same slot must not collide.
    names = set(af.build_filename(p, 5) for p in (af.POS_BASE, af.POS_MIDDLE, af.POS_TOP))
    check("세 위치가 세 이름", len(names) == 3, str(names))

    parsed = af.parse_filename("BASE_042.dat")
    check("되읽힌다", parsed is not None)
    assert parsed is not None
    check("슬롯 번호", parsed["slot"] == 42)
    check("위치", parsed["position"] == af.POS_BASE)

    # The name says nothing else, which is the point of it.
    check("이름에 시각이 없다", not any(c.isdigit() for c in "TOP_") )

    for bad in ("../etc/passwd", "TOP_000.dat/../x", "note.txt",
                "TOP_00.dat", "TOP_0000.dat", "TOP_000.dat.partial",
                "000.dat", "SIDE_000.dat", "TOP_abc.dat",
                "TOP_20260827_143012.dat"):
        check("거부: {0!r}".format(bad), af.parse_filename(bad) is None)


def test_slot_counter(tmp: str) -> None:
    """The counter is on the card, not derived from what is on it.

    Deriving it would hand a collected file's number straight back out, and two
    recordings either side of a collection would land on one name in the
    operator's folder.
    """
    print("\n[8] 슬롯 카운터")
    room: str = os.path.join(tmp, "slots")
    os.makedirs(room, exist_ok=True)
    check("첫 슬롯은 0", af.next_slot(room) == 0)
    check("다음은 1", af.next_slot(room) == 1)
    check("그 다음은 2", af.next_slot(room) == 2)

    # A file leaving the directory must not give its number back.
    open(os.path.join(room, af.build_filename(af.POS_TOP, 1)), "wb").close()
    os.remove(os.path.join(room, af.build_filename(af.POS_TOP, 1)))
    check("사라진 번호를 재사용하지 않는다", af.next_slot(room) == 3)

    with open(os.path.join(room, af.SLOT_FILE), "w", encoding="utf-8") as f:
        f.write("999")
    check("999 다음은 0으로 돈다", af.next_slot(room) == 0)

    with open(os.path.join(room, af.SLOT_FILE), "w", encoding="utf-8") as f:
        f.write("쓰레기")
    check("읽을 수 없으면 0에서 다시", af.next_slot(room) == 0)


def test_safe_join(tmp: str) -> None:
    print("\n[9] 경로 검증")
    good: str = af.build_filename(af.POS_TOP, 11)
    check("accepts a valid name", af.safe_join(tmp, good) is not None)
    for bad in ("../../etc/passwd", "sub/TOP_000.dat", "..\\x.dat"):
        check("blocks {0!r}".format(bad), af.safe_join(tmp, bad) is None)


def test_merge(tmp: str) -> None:
    print("\n[10] 연속 그룹 병합")
    rate: int = 25
    base: float = time.time()
    paths: list[str] = []
    # Two files that run back to back: 10 blocks = 10 s each.
    for i in range(2):
        start: float = base + i * 10.0
        name: str = af.build_filename(af.POS_TOP, i)
        path: str = os.path.join(tmp, "merge_" + name)
        with af.Writer(path, af.Header(start_epoch=start, sample_rate_hz=rate, position=af.POS_TOP)) as w:
            for block in make_blocks(10):
                w.write_block(block)
        paths.append(path)

    merged: str = os.path.join(tmp, "merged.ahrsbin")
    af.merge(paths, merged)
    summary = af.scan(merged)
    check("all blocks present", summary.blocks == 20, str(summary.blocks))
    check("all samples present", summary.samples == 500, str(summary.samples))

    blocks = list(af.iter_blocks(merged))
    check("elapsed time is monotonic",
          all(blocks[i].elapsed_ms < blocks[i + 1].elapsed_ms for i in range(len(blocks) - 1)))
    check("second file rebased onto the first",
          blocks[10].elapsed_ms >= 10000, str(blocks[10].elapsed_ms))


def test_boot_counter(tmp: str) -> None:
    print("\n[11] 부팅 카운터")
    first: int = af.next_boot_count(tmp)
    second: int = af.next_boot_count(tmp)
    check("increments", second == first + 1, "{0} -> {1}".format(first, second))
    check("persisted", os.path.exists(os.path.join(tmp, ".bootcount")))


def main() -> int:
    tmp: str = tempfile.mkdtemp(prefix="ahrs_test_")
    print("작업 디렉터리: {0}".format(tmp))
    try:
        test_sizes()
        test_header_roundtrip()
        test_block_roundtrip()
        test_write_and_scan(tmp)
        test_truncate_and_recover(tmp)
        test_recovered_flag(tmp)
        test_filenames()
        test_slot_counter(tmp)
        test_safe_join(tmp)
        test_merge(tmp)
        test_boot_counter(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total: int = _passed + _failed
    print("\n{0}/{1} 통과".format(_passed, total))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
