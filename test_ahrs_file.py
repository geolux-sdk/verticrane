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


def make_blocks(count: int, start_ms: int = 0, rate_hz: int = 25) -> list[af.Block]:
    period_ms: int = 1000 // rate_hz
    span: int = period_ms * (af.SAMPLES_PER_BLOCK - 1)
    return [
        af.Block(
            elapsed_ms=start_ms + i * period_ms * af.SAMPLES_PER_BLOCK,
            tilt=[0.100 + 0.001 * (i * af.SAMPLES_PER_BLOCK + j)
                  for j in range(af.SAMPLES_PER_BLOCK)],
            temp_c=23.5 + 0.1 * i,
            duration_ms=span,
        )
        for i in range(count)
    ]


def test_sizes() -> None:
    print("\n[1] 구조체 크기")
    check("header = 64 bytes", struct.calcsize(af._HEADER_FMT) == 64)
    check("block  = 128 bytes", struct.calcsize(af._BLOCK_FMT) == 128)
    check("samples per block = 25", af.SAMPLES_PER_BLOCK == 25)
    per_sample: float = af.BLOCK_SIZE / af.SAMPLES_PER_BLOCK
    check("5.1 bytes per sample", abs(per_sample - 5.12) < 0.01,
          "{0:.2f}".format(per_sample))


def test_header_roundtrip() -> None:
    print("\n[2] 헤더 왕복")
    h = af.Header(start_epoch=1772179369.29, sample_rate_hz=25)
    back = af.Header.unpack(h.pack())
    check("start epoch survives", abs(back.start_epoch - h.start_epoch) < 1e-6)
    check("sensor model survives", back.sensor_model == "HWT9037-485")
    check("baud survives", back.baud == 115200)

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
    check("tilt preserved", abs(back.tilt[7] - b.tilt[7]) < 1e-6)
    check("temperature preserved", abs(back.temp_c - b.temp_c) < 1e-4)

    corrupt = bytearray(b.pack())
    corrupt[40] ^= 0xFF
    check("corrupt block returns None", af.Block.unpack(bytes(corrupt)) is None)

    short = af.Block(elapsed_ms=0, tilt=[0.1, 0.2, 0.3], temp_c=20.0, duration_ms=80)
    back2 = af.Block.unpack(short.pack())
    assert back2 is not None
    check("short block keeps its count", back2.count == 3)


def test_write_and_scan(tmp: str) -> str:
    print("\n[4] 기록과 스캔")
    header = af.Header(start_epoch=time.time())
    path: str = os.path.join(tmp, "b0007_20260827_143012.unsynced.ahrsbin.partial")
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
    header = af.Header(start_epoch=time.time())
    path: str = os.path.join(tmp, "b0008_20260827_150000.unsynced.ahrsbin.partial")
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
    header = af.Header(start_epoch=device_start)
    path: str = os.path.join(tmp, "b0009_20260827_143012.unsynced.ahrsbin.partial")
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
    check("name uses the corrected time", name.startswith(expected_stamp), name)
    check(".unsynced dropped", ".unsynced" not in name, name)
    check("boot prefix dropped", not name.startswith("b"), name)
    check("sidecar followed the rename", os.path.exists(af.timeinfo_path(final)))

    start, quality = af.effective_start(final)
    check("effective start is corrected", abs(start - corrected) < 1.0)
    check("quality is trusted", quality in af.TRUSTED_QUALITIES)


def test_filenames() -> None:
    print("\n[7] 파일명 규칙")
    epoch: float = time.mktime(time.strptime("20260827_143012", "%Y%m%d_%H%M%S"))
    trusted: str = af.build_filename(epoch, af.QUALITY_SYNCED)
    check("trusted name", trusted == "20260827_143012.ahrsbin", trusted)
    untrusted: str = af.build_filename(epoch, af.QUALITY_UNSYNCED, boot_count=7)
    check("untrusted name", untrusted == "b0007_20260827_143012.unsynced.ahrsbin", untrusted)
    partial: str = af.build_filename(epoch, af.QUALITY_SYNCED, partial=True)
    check("partial name", partial.endswith(".ahrsbin.partial"), partial)

    parsed = af.parse_filename(untrusted)
    check("parses back", parsed is not None)
    assert parsed is not None
    check("boot count recovered", parsed["boot_count"] == 7)
    check("marked untrusted", not parsed["trusted"])

    for bad in ("../etc/passwd", "20260827_143012.ahrsbin/../x", "note.txt",
                "2026_143012.ahrsbin", "20260827_143012.ahrsbin.partial"):
        check("rejects {0!r}".format(bad), af.parse_filename(bad) is None)


def test_safe_join(tmp: str) -> None:
    print("\n[8] 경로 검증")
    good: str = af.build_filename(time.time(), af.QUALITY_SYNCED)
    check("accepts a valid name", af.safe_join(tmp, good) is not None)
    for bad in ("../../etc/passwd", "sub/20260827_143012.ahrsbin", "..\\x.ahrsbin"):
        check("blocks {0!r}".format(bad), af.safe_join(tmp, bad) is None)


def test_merge(tmp: str) -> None:
    print("\n[9] 연속 그룹 병합")
    rate: int = 25
    base: float = time.time()
    paths: list[str] = []
    # Two files that run back to back: 10 blocks = 10 s each.
    for i in range(2):
        start: float = base + i * 10.0
        name: str = af.build_filename(start, af.QUALITY_SYNCED)
        path: str = os.path.join(tmp, "merge_" + name)
        with af.Writer(path, af.Header(start_epoch=start, sample_rate_hz=rate)) as w:
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
    print("\n[10] 부팅 카운터")
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
