# coding:UTF-8
# Binary recording format for the AUTO_RUN tilt recorder (.ahrsbin).
#
# A file is a 64-byte header followed by fixed 128-byte blocks. One block holds
# 25 TILT samples plus the temperature that covers them, so the per-sample cost
# is 5.1 bytes -- about 11 MB/day at 25 Hz.
#
# The block is defined by sample COUNT, not by elapsed time: a block is written
# when 25 samples have been collected, however long that took. That is why there
# is no validity bitmap -- a block never has holes.
#
# Everything about wall-clock time lives OUTSIDE the binary. The header carries
# what the device believed at the moment recording started, which may be wrong
# (the Pi has no RTC). The authoritative start time is the one in the FILENAME,
# fixed up during boot recovery from the .timeinfo sidecar. See sections 3, 5
# and 6 of TILT_기록시스템_구현요구사항.md.

from __future__ import annotations

import json
import os
import re
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import BinaryIO, Iterator, Optional

from loguru import logger

MAGIC: bytes = b"AHRSBIN\0"
FORMAT_VERSION: int = 1
HEADER_SIZE: int = 64
BLOCK_SIZE: int = 128
SAMPLES_PER_BLOCK: int = 25
DEFAULT_SAMPLE_RATE_HZ: int = 25

# Header: magic, version, block size, samples/block, rate, start epoch, model,
# modbus address, baud, reserved, CRC32 over the preceding 60 bytes.
_HEADER_FMT: str = "<8sHHHHd16sHI14sI"
# Block: sample count, flags, duration (ms), reserved, elapsed (ms), temperature,
# 25 tilt values, reserved, CRC32 over the preceding 124 bytes.
_BLOCK_FMT: str = "<HHHHQf25fII"

assert struct.calcsize(_HEADER_FMT) == HEADER_SIZE
assert struct.calcsize(_BLOCK_FMT) == BLOCK_SIZE

# Block status flags (see section 5.2).
FLAG_READ_FAILED: int = 1 << 0    # a serial read failed while filling this block
FLAG_RECONNECTED: int = 1 << 1    # the serial link was re-established in this block
FLAG_UNSTABLE: int = 1 << 2       # this stretch was outside the stability limits

# Time quality. Only ever recorded in the filename and the .timeinfo sidecar --
# never in the header, so a rename can never contradict the file contents.
QUALITY_INVALID: str = "invalid"
QUALITY_UNSYNCED: str = "unsynced"
QUALITY_SYNCED: str = "synced"
QUALITY_RTC: str = "rtc"
TRUSTED_QUALITIES: frozenset[str] = frozenset({QUALITY_SYNCED, QUALITY_RTC})

EXT: str = ".ahrsbin"
PARTIAL_SUFFIX: str = ".partial"
TIMEINFO_SUFFIX: str = ".timeinfo"

# [bNNNN_]YYYYMMDD_HHMMSS[.unsynced][.recovered].ahrsbin  -- checked before any
# path is opened, so a request can never walk out of the data directory.
FILENAME_RE: re.Pattern[str] = re.compile(
    r"^(?:b(?P<boot>\d{4,})_)?"
    r"(?P<stamp>\d{8}_\d{6})"
    r"(?P<unsynced>\.unsynced)?"
    r"(?P<recovered>\.recovered)?"
    r"\.ahrsbin$"
)
_STAMP_FMT: str = "%Y%m%d_%H%M%S"


class FormatError(Exception):
    """The bytes on disk are not a usable .ahrsbin."""


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

@dataclass
class Header:
    # What the device believed when recording started. May be wrong; the
    # filename wins if the two disagree (section 5.2).
    start_epoch: float = 0.0
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ
    samples_per_block: int = SAMPLES_PER_BLOCK
    block_size: int = BLOCK_SIZE
    version: int = FORMAT_VERSION
    sensor_model: str = "HWT9037-485"
    modbus_addr: int = 0x50
    baud: int = 115200

    def pack(self) -> bytes:
        body: bytes = struct.pack(
            _HEADER_FMT[:-1],  # everything except the trailing CRC field
            MAGIC, self.version, self.block_size, self.samples_per_block,
            self.sample_rate_hz, self.start_epoch,
            self.sensor_model.encode("ascii", "replace")[:16],
            self.modbus_addr, self.baud, b"\0" * 14,
        )
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    @classmethod
    def unpack(cls, raw: bytes) -> "Header":
        if len(raw) != HEADER_SIZE:
            raise FormatError("header is {0} bytes, expected {1}".format(len(raw), HEADER_SIZE))
        (magic, version, block_size, samples_per_block, rate, start_epoch,
         model, addr, baud, _reserved, crc) = struct.unpack(_HEADER_FMT, raw)
        if magic != MAGIC:
            raise FormatError("bad magic {0!r}".format(magic))
        if zlib.crc32(raw[:-4]) & 0xFFFFFFFF != crc:
            raise FormatError("header CRC mismatch")
        if block_size != BLOCK_SIZE:
            raise FormatError("unsupported block size {0}".format(block_size))
        return cls(
            start_epoch=start_epoch,
            sample_rate_hz=rate,
            samples_per_block=samples_per_block,
            block_size=block_size,
            version=version,
            sensor_model=model.rstrip(b"\0").decode("ascii", "replace"),
            modbus_addr=addr,
            baud=baud,
        )


# --------------------------------------------------------------------------
# Block
# --------------------------------------------------------------------------

@dataclass
class Block:
    elapsed_ms: int = 0            # elapsed time of the block's FIRST sample
    tilt: list[float] = field(default_factory=list)
    temp_c: float = 0.0
    duration_ms: int = 0           # first sample -> last sample
    flags: int = 0

    @property
    def count(self) -> int:
        return len(self.tilt)

    def pack(self) -> bytes:
        if len(self.tilt) > SAMPLES_PER_BLOCK:
            raise ValueError("block holds at most {0} samples".format(SAMPLES_PER_BLOCK))
        # A short block only happens on a graceful stop; pad the unused slots.
        padded: list[float] = list(self.tilt) + [0.0] * (SAMPLES_PER_BLOCK - len(self.tilt))
        body: bytes = struct.pack(
            _BLOCK_FMT[:-1],
            len(self.tilt), self.flags, min(self.duration_ms, 0xFFFF), 0,
            self.elapsed_ms, self.temp_c, *padded, 0,
        )
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    @classmethod
    def unpack(cls, raw: bytes) -> Optional["Block"]:
        # Returns None for a torn or corrupt block so callers can stop there
        # rather than raise -- a truncated tail is the expected power-cut case.
        if len(raw) != BLOCK_SIZE:
            return None
        if zlib.crc32(raw[:-4]) & 0xFFFFFFFF != struct.unpack_from("<I", raw, BLOCK_SIZE - 4)[0]:
            return None
        values = struct.unpack(_BLOCK_FMT, raw)
        count, flags, duration, _res0, elapsed, temp = values[:6]
        tilt = values[6:6 + SAMPLES_PER_BLOCK]
        if count > SAMPLES_PER_BLOCK:
            return None
        return cls(elapsed_ms=elapsed, tilt=list(tilt[:count]), temp_c=temp,
                   duration_ms=duration, flags=flags)

    def sample_times_ms(self, sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ) -> list[float]:
        # Nominal spacing is one tick; when the block ran long (a failed read
        # stretched it) spreading the measured duration is closer to reality.
        if self.count < 2:
            return [float(self.elapsed_ms)] * self.count
        nominal: float = 1000.0 / sample_rate_hz
        step: float = self.duration_ms / (self.count - 1) if self.duration_ms else nominal
        return [self.elapsed_ms + i * step for i in range(self.count)]


# --------------------------------------------------------------------------
# Filenames
# --------------------------------------------------------------------------

def build_filename(start_epoch: float, quality: str, boot_count: int = 0,
                   recovered: bool = False, partial: bool = False) -> str:
    """Compose a filename from a start time and how much that time is trusted."""
    stamp: str = time.strftime(_STAMP_FMT, time.localtime(start_epoch))
    name: str = stamp
    if quality not in TRUSTED_QUALITIES:
        # The boot counter keeps names unique and orderable when the clock is
        # not usable -- fake-hwclock can restore the same value every boot.
        name = "b{0:04d}_{1}".format(boot_count, stamp)
    if recovered:
        name += ".recovered"
    if quality not in TRUSTED_QUALITIES:
        name += ".unsynced"
    name += EXT
    if partial:
        name += PARTIAL_SUFFIX
    return name


def parse_filename(name: str) -> Optional[dict]:
    """Pull the start time and trust level back out of a filename.

    Returns None when the name does not match exactly, which is also the
    path-traversal guard: no separators or dots can survive this.
    """
    m = FILENAME_RE.match(os.path.basename(name))
    if m is None:
        return None
    try:
        start_epoch: float = time.mktime(time.strptime(m.group("stamp"), _STAMP_FMT))
    except ValueError:
        return None
    return {
        "start_epoch": start_epoch,
        "boot_count": int(m.group("boot")) if m.group("boot") else 0,
        "trusted": m.group("unsynced") is None,
        "recovered": m.group("recovered") is not None,
    }


def safe_join(data_dir: str, name: str) -> Optional[str]:
    """Resolve a requested filename inside data_dir, or None if it escapes.

    Anything carrying a directory component is rejected outright rather than
    stripped down to its basename. Silently normalising a hostile name is how
    a traversal hole survives the next refactor.
    """
    if name != os.path.basename(name) or os.path.sep in name or "/" in name:
        return None
    if parse_filename(name) is None:
        return None
    root: str = os.path.realpath(data_dir)
    path: str = os.path.realpath(os.path.join(root, name))
    # Verified again after resolution: a symlink could still point outside.
    if os.path.dirname(path) != root:
        return None
    return path


# --------------------------------------------------------------------------
# Time info sidecar
# --------------------------------------------------------------------------

def timeinfo_path(data_path: str) -> str:
    # Sits next to the recording, including while it is still .partial.
    return data_path + TIMEINFO_SUFFIX


def write_timeinfo(data_path: str, info: dict) -> None:
    """Replace the sidecar atomically: temp file -> fsync -> rename -> dir fsync."""
    path: str = timeinfo_path(data_path)
    tmp: str = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path) or ".")


def read_timeinfo(data_path: str) -> Optional[dict]:
    try:
        with open(timeinfo_path(data_path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        # A missing or damaged sidecar costs only the corrected time; the
        # recording itself stays intact and keeps its .unsynced name.
        return None


def _fsync_dir(path: str) -> None:
    # A rename is not durable until the directory entry itself is flushed.
    try:
        fd: int = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------

class Writer:
    """Appends blocks to a .partial file.

    Never renames while recording: the rename that fixes the timestamp happens
    during boot recovery, when nothing is being written (section 3).
    """

    def __init__(self, path: str, header: Header, fsync_interval_s: float = 1.0) -> None:
        self.path: str = path
        self.header: Header = header
        self.fsync_interval_s: float = fsync_interval_s
        self.blocks_written: int = 0
        self.samples_written: int = 0
        self._last_fsync: float = time.monotonic()
        new: bool = not os.path.exists(path)
        self._f: BinaryIO = open(path, "r+b" if not new else "w+b")
        if new:
            self._f.write(header.pack())
            self._f.flush()
            os.fsync(self._f.fileno())
            _fsync_dir(os.path.dirname(os.path.abspath(path)))
            logger.info("Recording to {}", os.path.basename(path))
        else:
            self._f.seek(0, os.SEEK_END)

    def write_block(self, block: Block) -> None:
        self._f.write(block.pack())
        self.blocks_written += 1
        self.samples_written += block.count
        now: float = time.monotonic()
        if now - self._last_fsync >= self.fsync_interval_s:
            self.sync()
            self._last_fsync = now

    def sync(self) -> None:
        self._f.flush()
        os.fsync(self._f.fileno())

    def close(self) -> None:
        # close() alone does not reach the disk, so sync first (section 6).
        try:
            self.sync()
        finally:
            self._f.close()

    def __enter__(self) -> "Writer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------

def read_header(path: str) -> Header:
    with open(path, "rb") as f:
        return Header.unpack(f.read(HEADER_SIZE))


def iter_blocks(path: str) -> Iterator[Block]:
    """Yield every intact block, stopping at the first damaged or torn one."""
    with open(path, "rb") as f:
        Header.unpack(f.read(HEADER_SIZE))
        while True:
            raw: bytes = f.read(BLOCK_SIZE)
            if len(raw) < BLOCK_SIZE:
                return
            block: Optional[Block] = Block.unpack(raw)
            if block is None:
                return
            yield block


@dataclass
class Summary:
    header: Header
    blocks: int = 0
    samples: int = 0
    first_elapsed_ms: int = 0
    last_elapsed_ms: int = 0
    last_sample_elapsed_ms: float = 0.0
    valid_bytes: int = HEADER_SIZE
    truncated: bool = False        # trailing bytes were damaged or incomplete

    @property
    def duration_s(self) -> float:
        return self.last_sample_elapsed_ms / 1000.0


def scan(path: str) -> Summary:
    """Walk the file and report how far the intact data reaches."""
    header: Header = read_header(path)
    summary = Summary(header=header)
    size: int = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(HEADER_SIZE)
        first: bool = True
        while True:
            raw: bytes = f.read(BLOCK_SIZE)
            if len(raw) < BLOCK_SIZE:
                break
            block: Optional[Block] = Block.unpack(raw)
            if block is None:
                break
            if first:
                summary.first_elapsed_ms = block.elapsed_ms
                first = False
            summary.blocks += 1
            summary.samples += block.count
            summary.last_elapsed_ms = block.elapsed_ms
            summary.last_sample_elapsed_ms = block.elapsed_ms + block.duration_ms
            summary.valid_bytes += BLOCK_SIZE
    summary.truncated = summary.valid_bytes != size
    return summary


def effective_start(path: str) -> tuple[float, str]:
    """Best known start time for a file, and how much it is trusted.

    The filename wins over the header: the header holds what the device
    believed at the time, the name holds what recovery worked out (section 5.2).
    """
    parsed: Optional[dict] = parse_filename(path)
    if parsed is not None:
        quality: str = QUALITY_SYNCED if parsed["trusted"] else QUALITY_UNSYNCED
        return float(parsed["start_epoch"]), quality
    info: Optional[dict] = read_timeinfo(path)
    if info and info.get("corrected_start_epoch"):
        return float(info["corrected_start_epoch"]), str(info.get("quality", QUALITY_SYNCED))
    return read_header(path).start_epoch, QUALITY_UNSYNCED


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------

def recover_partial(partial_path: str, corrupt_dir: Optional[str] = None) -> Optional[str]:
    """Verify a leftover .partial, trim the torn tail, and give it its final name.

    This is the normal end of a recording: in the field the power simply drops,
    so nothing gets finalised at shutdown (section 6). Returns the new path, or
    None if the file was unusable and got moved aside.
    """
    directory: str = os.path.dirname(os.path.abspath(partial_path))
    base: str = os.path.basename(partial_path)
    if not base.endswith(PARTIAL_SUFFIX):
        raise ValueError("not a .partial file: {0}".format(base))

    try:
        summary: Summary = scan(partial_path)
    except (FormatError, OSError) as exc:
        logger.error("Cannot read {}: {}", base, exc)
        _quarantine(partial_path, corrupt_dir)
        return None

    if summary.blocks == 0:
        # Recording stopped before the first block was ever flushed.
        logger.warning("{} holds no complete block; discarding", base)
        _quarantine(partial_path, corrupt_dir)
        return None

    if summary.truncated:
        # Drop the torn tail so every remaining block passes its CRC.
        with open(partial_path, "r+b") as f:
            f.truncate(summary.valid_bytes)
            os.fsync(f.fileno())
        logger.info("{}: trimmed to {} intact blocks", base, summary.blocks)

    info: Optional[dict] = read_timeinfo(partial_path)
    if info and info.get("corrected_start_epoch"):
        start_epoch: float = float(info["corrected_start_epoch"])
        quality: str = str(info.get("quality", QUALITY_SYNCED))
        logger.info("{}: applying {:+.2f}s correction from .timeinfo",
                    base, float(info.get("offset_seconds", 0.0)))
    else:
        # No usable correction: keep the device's own idea of the time. The
        # data is intact, only the timestamp stays untrusted -- and the
        # .unsynced suffix says so.
        start_epoch = summary.header.start_epoch
        quality = QUALITY_UNSYNCED
        logger.warning("{}: no time correction available; staying unsynced", base)

    boot_count: int = int(info.get("boot_count", 0)) if info else 0
    final_name: str = build_filename(start_epoch, quality, boot_count, recovered=True)
    final_path: str = os.path.join(directory, final_name)
    final_path = _unique_path(final_path)

    os.replace(partial_path, final_path)
    old_sidecar: str = timeinfo_path(partial_path)
    if os.path.exists(old_sidecar):
        os.replace(old_sidecar, timeinfo_path(final_path))
    _fsync_dir(directory)
    logger.info("Recovered {} -> {}", base, os.path.basename(final_path))
    return final_path


def _unique_path(path: str) -> str:
    # Two recordings can land on the same name when the clock is untrusted and
    # the boot counter did not advance. Never overwrite an existing recording.
    if not os.path.exists(path):
        return path
    stem, ext = path[:-len(EXT)], EXT
    for n in range(2, 1000):
        candidate: str = "{0}_{1}{2}".format(stem, n, ext)
        if not os.path.exists(candidate):
            return candidate
    raise FormatError("cannot find a free name for {0}".format(path))


def _quarantine(path: str, corrupt_dir: Optional[str]) -> None:
    if corrupt_dir is None:
        corrupt_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "corrupt")
    os.makedirs(corrupt_dir, exist_ok=True)
    target: str = os.path.join(corrupt_dir, os.path.basename(path))
    try:
        os.replace(path, target)
        sidecar: str = timeinfo_path(path)
        if os.path.exists(sidecar):
            os.replace(sidecar, timeinfo_path(target))
        logger.warning("Moved {} to {}", os.path.basename(path), corrupt_dir)
    except OSError as exc:
        logger.error("Could not quarantine {}: {}", path, exc)


def recover_all(data_dir: str) -> list[str]:
    """Recover every leftover .partial. Runs at boot, before the HTTP wait."""
    recovered: list[str] = []
    for name in sorted(os.listdir(data_dir)):
        if not name.endswith(EXT + PARTIAL_SUFFIX):
            continue
        path: Optional[str] = recover_partial(os.path.join(data_dir, name))
        if path is not None:
            recovered.append(path)
    return recovered


# --------------------------------------------------------------------------
# Grouping and merging
# --------------------------------------------------------------------------

def group_contiguous(paths: list[str], gap_tolerance_s: float = 2.0) -> list[list[str]]:
    """Split files into runs that are continuous in time (section 5.4)."""
    entries: list[tuple[float, str, Summary]] = []
    for path in paths:
        try:
            summary: Summary = scan(path)
        except (FormatError, OSError) as exc:
            logger.warning("Skipping {}: {}", os.path.basename(path), exc)
            continue
        entries.append((effective_start(path)[0], path, summary))
    entries.sort(key=lambda e: e[0])

    groups: list[list[str]] = []
    current: list[str] = []
    prev_end: float = 0.0
    for start, path, summary in entries:
        period: float = 1.0 / max(summary.header.sample_rate_hz, 1)
        if current and abs(start - (prev_end + period)) > gap_tolerance_s:
            groups.append(current)
            current = []
        current.append(path)
        prev_end = start + summary.duration_s
    if current:
        groups.append(current)
    return groups


def merge(paths: list[str], out_path: str) -> str:
    """Concatenate a contiguous group into one valid .ahrsbin.

    The first file's header is kept; later files lose theirs and have every
    block's elapsed time rebased onto the first file's origin. Because blocks
    are fixed size, only that one field and the CRC need rewriting.
    """
    if not paths:
        raise ValueError("nothing to merge")
    base_start: float = effective_start(paths[0])[0]
    with open(out_path, "wb") as out:
        out.write(read_header(paths[0]).pack())
        for path in paths:
            offset_ms: int = int(round((effective_start(path)[0] - base_start) * 1000.0))
            for block in iter_blocks(path):
                block.elapsed_ms += offset_ms
                out.write(block.pack())
        out.flush()
        os.fsync(out.fileno())
    return out_path


# --------------------------------------------------------------------------
# Boot counter
# --------------------------------------------------------------------------

def next_boot_count(data_dir: str) -> int:
    """Increment and return the persistent boot counter (section 5.1)."""
    path: str = os.path.join(data_dir, ".bootcount")
    try:
        with open(path, encoding="utf-8") as f:
            count: int = int(f.read().strip() or 0)
    except (OSError, ValueError):
        count = 0
    count += 1
    tmp: str = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(count))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(data_dir)
    return count
