# coding:UTF-8
# Self-check for the .ahrsbin format.
#
#   python test_ahrs_file.py
#
# Walks the sequence section 13 of the requirements asks for: write -> cut the
# file mid-block -> recover -> apply .timeinfo -> merge -> corrected filename.
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
    path: str = os.path.join(tmp, "TOP_b0007_20260827_143012.unsynced.ahrsbin.partial")
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
    path: str = os.path.join(tmp, "TOP_b0008_20260827_150000.unsynced.ahrsbin.partial")
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
    check("marked .recovered", ".recovered" in name)
    check("still .unsynced (no timeinfo)", ".unsynced" in name, name)
    check("torn tail removed", not af.scan(final).truncated)
    check("data intact", af.scan(final).blocks == 10)


def test_timeinfo_recovery(tmp: str) -> None:
    print("\n[6] .timeinfo 적용 → 이름 보정")
    device_start: float = time.mktime(time.strptime("20260827_143012", "%Y%m%d_%H%M%S"))
    corrected: float = device_start + 743.19
    header = af.Header(start_epoch=device_start, position=af.POS_TOP)
    path: str = os.path.join(tmp, "TOP_b0009_20260827_143012.unsynced.ahrsbin.partial")
    with af.Writer(path, header) as w:
        for block in make_blocks(5):
            w.write_block(block)
    af.write_timeinfo(path, {
        "device_start_epoch": device_start,
        "corrected_start_epoch": corrected,
        "offset_seconds": 743.19,
        "quality": af.QUALITY_SYNCED,
        "source": "ntp",
        "applied_at_elapsed_ms": 3000,
        "original_filename": os.path.basename(path),
        "boot_count": 9,
    })

    final = af.recover_partial(path)
    assert final is not None
    name: str = os.path.basename(final)
    expected_stamp: str = time.strftime("%Y%m%d_%H%M%S", time.localtime(corrected))
    check("name uses the corrected time", name.startswith("TOP_" + expected_stamp), name)
    check(".unsynced dropped", ".unsynced" not in name, name)
    check("boot prefix dropped", "_b0009_" not in name, name)
    check("position kept", name.startswith("TOP_"), name)
    check("sidecar followed the rename", os.path.exists(af.timeinfo_path(final)))

    start, quality = af.effective_start(final)
    check("effective start is corrected", abs(start - corrected) < 1.0)
    check("quality is trusted", quality in af.TRUSTED_QUALITIES)


def test_filenames() -> None:
    print("\n[7] 파일명 규칙")
    epoch: float = time.mktime(time.strptime("20260827_143012", "%Y%m%d_%H%M%S"))
    trusted: str = af.build_filename(epoch, af.QUALITY_SYNCED, af.POS_TOP)
    check("trusted name", trusted == "TOP_20260827_143012.ahrsbin", trusted)
    untrusted: str = af.build_filename(epoch, af.QUALITY_UNSYNCED, af.POS_BASE, 7)
    check("untrusted name",
          untrusted == "BASE_b0007_20260827_143012.unsynced.ahrsbin", untrusted)
    unset: str = af.build_filename(epoch, af.QUALITY_SYNCED, af.POS_UNSET)
    check("unset position is visible", unset.startswith("UNSET_"), unset)
    partial: str = af.build_filename(epoch, af.QUALITY_SYNCED, af.POS_TOP, partial=True)
    check("partial name", partial.endswith(".ahrsbin.partial"), partial)

    # Three devices recording at the same instant must not collide.
    names = set(af.build_filename(epoch, af.QUALITY_SYNCED, p)
                for p in (af.POS_BASE, af.POS_MIDDLE, af.POS_TOP))
    check("three positions give three names", len(names) == 3, str(names))

    parsed = af.parse_filename(untrusted)
    check("parses back", parsed is not None)
    assert parsed is not None
    check("boot count recovered", parsed["boot_count"] == 7)
    check("position recovered", parsed["position"] == af.POS_BASE)
    check("marked untrusted", not parsed["trusted"])

    # A recording that began on an untrusted clock and then lost its tail to a
    # power cut carries both marks. This is the ordinary field case -- power up
    # away from WiFi, power down without warning -- and once it failed to parse
    # the file was on the card but absent from the operator's list.
    both: str = af.build_filename(epoch, af.QUALITY_UNSYNCED, af.POS_TOP, 34,
                                  recovered=True)
    parsed = af.parse_filename(both)
    check("recovered+unsynced parses", parsed is not None, both)
    assert parsed is not None
    check("both: untrusted", not parsed["trusted"], both)
    check("both: recovered", parsed["recovered"], both)
    check("both: boot count kept", parsed["boot_count"] == 34, both)
    check("both: position kept", parsed["position"] == af.POS_TOP, both)
    check("both: start time kept", abs(parsed["start_epoch"] - epoch) < 1.0, both)

    # Either order reads the same, so reordering the two marks cannot silently
    # hide a file again.
    swapped: str = "TOP_b0034_20260827_143012.unsynced.recovered.ahrsbin"
    other = af.parse_filename(swapped)
    check("mark order does not matter", other is not None, swapped)
    assert other is not None
    check("swapped: untrusted", not other["trusted"], swapped)
    check("swapped: recovered", other["recovered"], swapped)

    # _unique_path's collision counter, which lands on exactly these names:
    # an untrusted clock plus a boot counter that did not advance.
    duped: str = "TOP_b0034_20260827_143012.recovered.unsynced_2.ahrsbin"
    dparsed = af.parse_filename(duped)
    check("collision counter parses", dparsed is not None, duped)
    assert dparsed is not None
    check("duped: recovered", dparsed["recovered"], duped)

    for bad in ("../etc/passwd", "TOP_20260827_143012.ahrsbin/../x", "note.txt",
                "TOP_2026_143012.ahrsbin", "TOP_20260827_143012.ahrsbin.partial",
                "20260827_143012.ahrsbin", "SIDE_20260827_143012.ahrsbin",
                "TOP_20260827_143012.recovered.recovered.ahrsbin",
                "TOP_20260827_143012.pending.ahrsbin"):
        check("rejects {0!r}".format(bad), af.parse_filename(bad) is None)


def test_apply_correction(tmp: str) -> None:
    """A file finished before the clock was known still gets its real time.

    The field case: record out of range, stop, drive back into range. Nothing
    revisits a finalised name on its own, so without this the recording stayed
    untrusted for good even though the offset was measured minutes later.
    """
    print("\n[8] 뒤늦은 시각 보정")
    device_start: float = time.mktime(time.strptime("20260828_090000", "%Y%m%d_%H%M%S"))
    name: str = af.build_filename(device_start, af.QUALITY_UNSYNCED, af.POS_TOP, 41,
                                  recovered=True)
    path: str = os.path.join(tmp, name)
    with af.Writer(path, af.Header(start_epoch=device_start,
                                   position=af.POS_TOP)) as w:
        for block in make_blocks(3):
            w.write_block(block)
    af.write_timeinfo(path, {"device_start_epoch": device_start,
                             "quality": af.QUALITY_UNSYNCED, "boot_count": 41})

    offset: float = 137.0
    final = af.apply_correction(path, offset)
    assert final is not None
    new_name: str = os.path.basename(final)
    check("보정된 파일이 있다", os.path.exists(final), new_name)
    check("원래 파일은 사라졌다", not os.path.exists(path), name)
    check(".unsynced 가 떨어졌다", ".unsynced" not in new_name, new_name)
    check("부팅 번호가 떨어졌다", "b0041" not in new_name, new_name)
    check(".recovered 는 남는다", ".recovered" in new_name, new_name)
    check("위치는 그대로", new_name.startswith("TOP_"), new_name)

    parsed = af.parse_filename(new_name)
    assert parsed is not None
    check("이제 신뢰됨", parsed["trusted"], new_name)
    check("시작 시각이 보정만큼 옮겨졌다",
          abs(parsed["start_epoch"] - (device_start + offset)) < 1.0,
          str(parsed["start_epoch"]))

    start, quality = af.effective_start(final)
    check("effective_start 도 보정값", abs(start - (device_start + offset)) < 1.0)
    check("품질이 신뢰됨", quality in af.TRUSTED_QUALITIES, quality)

    info = af.read_timeinfo(final)
    assert info is not None
    check("사이드카가 따라왔다", abs(float(info["offset_seconds"]) - offset) < 0.01)
    check("장치가 믿었던 시각은 보존", 
          abs(float(info["device_start_epoch"]) - device_start) < 1.0)

    # Already trusted: nothing to do, and re-timing one would move a good time.
    good: str = af.build_filename(device_start, af.QUALITY_SYNCED, af.POS_TOP)
    good_path: str = os.path.join(tmp, good)
    with af.Writer(good_path, af.Header(start_epoch=device_start,
                                        position=af.POS_TOP)) as w:
        for block in make_blocks(2):
            w.write_block(block)
    check("이미 신뢰된 파일은 건드리지 않는다",
          af.apply_correction(good_path, offset) is None)
    check("그 파일은 그대로 있다", os.path.exists(good_path), good)


def test_safe_join(tmp: str) -> None:
    print("\n[9] 경로 검증")
    good: str = af.build_filename(time.time(), af.QUALITY_SYNCED, af.POS_TOP)
    check("accepts a valid name", af.safe_join(tmp, good) is not None)
    for bad in ("../../etc/passwd", "sub/TOP_20260827_143012.ahrsbin", "..\\x.ahrsbin"):
        check("blocks {0!r}".format(bad), af.safe_join(tmp, bad) is None)


def test_merge(tmp: str) -> None:
    print("\n[10] 연속 그룹 병합")
    rate: int = 25
    base: float = time.time()
    paths: list[str] = []
    # Two files that run back to back: 10 blocks = 10 s each.
    for i in range(2):
        start: float = base + i * 10.0
        name: str = af.build_filename(start, af.QUALITY_SYNCED, af.POS_TOP)
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
        test_timeinfo_recovery(tmp)
        test_filenames()
        test_apply_correction(tmp)
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
