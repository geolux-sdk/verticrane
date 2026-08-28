# coding:UTF-8
# Binary recording format for the AUTO_RUN tilt recorder (.ahrsbin).
#
# A file is a 64-byte header followed by fixed 1232-byte blocks. One block holds
# 25 samples of everything the sensor reports -- acceleration, angular velocity,
# magnetic field and Roll/Pitch/Yaw -- plus the temperature that covers them.
#
# The block is defined by sample COUNT, not by elapsed time: it goes out when 25
# samples have been collected, however long that took. That is why there is no
# validity bitmap -- a block never has holes.
#
# Values are stored decoded rather than as raw registers. Raw would be smaller
# and bit-exact, but the scale factors live in configuration registers (gyro
# range 0x20, accel range 0x21); change a range and every earlier file needs a
# different factor. Decoded, the number in the file is the physical quantity.
#
# Everything about wall-clock time lives OUTSIDE the binary. The header carries
# what the device believed at the moment recording started, which may be wrong
# (the Pi has no RTC). The authoritative start time is the one in the FILENAME,
# established once when the file is opened and never revised. See sections 3, 5
# and 6 of TILT_기록시스템_구현요구사항.md.

from __future__ import annotations

import json
import math
import os
import re
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import BinaryIO, Iterator, Optional

from loguru import logger

MAGIC: bytes = b"AHRSBIN\0"
FORMAT_VERSION: int = 2
HEADER_SIZE: int = 64
BLOCK_SIZE: int = 1232
SAMPLES_PER_BLOCK: int = 25
FLOATS_PER_SAMPLE: int = 12
DEFAULT_SAMPLE_RATE_HZ: int = 25

# Header: magic, version, block size, samples/block, rate, start epoch,
# SENSOR_ID, SENSOR_FLAG, device serial, file flags, reserved, CRC32 over the
# first 60 bytes.
_HEADER_FMT: str = "<8sHHHHd16sH12sB5sI"
# Block: count, flags, duration (ms), reserved, elapsed (ms), temperature,
# reserved, 25 x 12 floats, reserved, CRC32 over the first 1228 bytes.
_BLOCK_FMT: str = "<HHHHQfI{0}fII".format(SAMPLES_PER_BLOCK * FLOATS_PER_SAMPLE)

assert struct.calcsize(_HEADER_FMT) == HEADER_SIZE
assert struct.calcsize(_BLOCK_FMT) == BLOCK_SIZE

# Block status flags (see section 5.2).
FLAG_READ_FAILED: int = 1 << 0    # a serial read failed while filling this block
FLAG_RECONNECTED: int = 1 << 1    # the serial link was re-established in this block
FLAG_UNSTABLE: int = 1 << 2       # this stretch was outside the stability limits

# Where on the crane this device sits. Three of them go on one structure, so the
# position is what tells three otherwise identical recordings apart.
POS_UNSET: int = 0
POS_BASE: int = 1
POS_MIDDLE: int = 2
POS_TOP: int = 3
POSITION_NAMES: dict[int, str] = {POS_UNSET: "UNSET", POS_BASE: "BASE",
                                  POS_MIDDLE: "MIDDLE", POS_TOP: "TOP"}
POSITION_VALUES: dict[str, int] = {v.lower(): k for k, v in POSITION_NAMES.items()}

# File-level status, in the header rather than beside it. One fact lives here:
# a recording that lost its end to a power cut. It is known only at recovery,
# which is the one moment nothing else is touching the file, so the header is
# rewritten once and never again.
FILE_FLAG_RECOVERED: int = 1 << 0

EXT: str = ".dat"
PARTIAL_SUFFIX: str = ".partial"

# Recordings occupy numbered slots and the numbering goes round. The device is
# a buffer between the crane and the operator's laptop, not a store: a file
# still here a thousand recordings later is a collection that never happened,
# and holding on to it at the cost of the recording being made now would be
# the wrong way round.
SLOT_COUNT: int = 1000
SLOT_FILE: str = ".slot"

# FLAG_NNN.dat -- checked before any path is opened, so a request can never
# walk out of the data directory. Nothing else is encoded here: the name is an
# address on the card, and what the recording *is* lives inside it.
FILENAME_RE: re.Pattern[str] = re.compile(
    r"^(?P<flag>UNSET|BASE|MIDDLE|TOP)_(?P<slot>\d{3})\.dat$"
)


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
    sensor_id: str = ""
    position: int = POS_UNSET
    # Read from registers 0x7F~0x84, low byte first within each register. High
    # byte first also yields a plausible string, which is exactly why getting it
    # wrong goes unnoticed -- check it against the label on the device.
    device_serial: str = ""
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ
    samples_per_block: int = SAMPLES_PER_BLOCK
    block_size: int = BLOCK_SIZE
    version: int = FORMAT_VERSION
    flags: int = 0                 # FILE_FLAG_*, set once by recovery

    @property
    def recovered(self) -> bool:
        return bool(self.flags & FILE_FLAG_RECOVERED)

    @property
    def position_name(self) -> str:
        return POSITION_NAMES.get(self.position, "UNSET")

    def pack(self) -> bytes:
        body: bytes = struct.pack(
            _HEADER_FMT[:-1],  # everything except the trailing CRC field
            MAGIC, self.version, self.block_size, self.samples_per_block,
            self.sample_rate_hz, self.start_epoch,
            self.sensor_id.encode("ascii", "replace")[:16],
            self.position,
            self.device_serial.encode("ascii", "replace")[:12],
            self.flags,
            b"\0" * 5,
        )
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    @classmethod
    def unpack(cls, raw: bytes) -> "Header":
        if len(raw) != HEADER_SIZE:
            raise FormatError("header is {0} bytes, expected {1}".format(len(raw), HEADER_SIZE))
        (magic, version, block_size, samples_per_block, rate, start_epoch,
         sensor_id, position, serial, flags, _reserved, crc) = struct.unpack(
            _HEADER_FMT, raw)
        if magic != MAGIC:
            raise FormatError("bad magic {0!r}".format(magic))
        if zlib.crc32(raw[:-4]) & 0xFFFFFFFF != crc:
            raise FormatError("header CRC mismatch")
        if block_size != BLOCK_SIZE:
            raise FormatError("unsupported block size {0}".format(block_size))
        return cls(
            start_epoch=start_epoch,
            sensor_id=sensor_id.rstrip(b"\0").decode("ascii", "replace"),
            position=position,
            device_serial=serial.rstrip(b"\0").decode("ascii", "replace"),
            sample_rate_hz=rate,
            samples_per_block=samples_per_block,
            block_size=block_size,
            version=version,
            flags=flags,
        )


# --------------------------------------------------------------------------
# Sample and block
# --------------------------------------------------------------------------

@dataclass
class Sample:
    """One poll of the sensor: everything readReg(0x34, 15) returns."""
    acc: tuple[float, float, float] = (0.0, 0.0, 0.0)      # g
    gyro: tuple[float, float, float] = (0.0, 0.0, 0.0)     # deg/s
    mag: tuple[float, float, float] = (0.0, 0.0, 0.0)      # LSB
    roll: float = 0.0                                      # deg
    pitch: float = 0.0
    yaw: float = 0.0

    @property
    def tilt_pct(self) -> float:
        # Not stored: it is a function of roll and pitch, and a stored copy could
        # disagree with its own inputs. Computed on the way out instead.
        return tilt_pct(self.roll, self.pitch)

    def as_floats(self) -> tuple[float, ...]:
        return self.acc + self.gyro + self.mag + (self.roll, self.pitch, self.yaw)

    @classmethod
    def from_floats(cls, v: tuple[float, ...]) -> "Sample":
        return cls(acc=(v[0], v[1], v[2]), gyro=(v[3], v[4], v[5]),
                   mag=(v[6], v[7], v[8]), roll=v[9], pitch=v[10], yaw=v[11])


def tilt_pct(roll_deg: float, pitch_deg: float) -> float:
    """Combined tilt as a slope percentage, independent of direction.

    The same value log_tilt.py has always written, so old CSVs and new .ahrsbin
    files mean the same thing.
    """
    sx: float = math.tan(math.radians(roll_deg))
    sy: float = math.tan(math.radians(pitch_deg))
    return math.hypot(sx, sy) * 100.0


@dataclass
class Block:
    elapsed_ms: int = 0            # elapsed time of the block's FIRST sample
    samples: list[Sample] = field(default_factory=list)
    temp_c: float = 0.0            # one per block: the sensor updates it at 1 Hz
    duration_ms: int = 0           # first sample -> last sample
    flags: int = 0

    @property
    def count(self) -> int:
        return len(self.samples)

    def pack(self) -> bytes:
        # Always exactly 25. A block that never filled is dropped rather than
        # padded, which costs under a second and removes every partial-block
        # branch from both sides of the format.
        if len(self.samples) != SAMPLES_PER_BLOCK:
            raise ValueError("a block holds exactly {0} samples, got {1}".format(
                SAMPLES_PER_BLOCK, len(self.samples)))
        values: list[float] = []
        for sample in self.samples:
            values.extend(sample.as_floats())
        body: bytes = struct.pack(
            _BLOCK_FMT[:-1],
            len(self.samples), self.flags, min(self.duration_ms, 0xFFFF), 0,
            self.elapsed_ms, self.temp_c, 0, *values, 0,
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
        count, flags, duration, _res0, elapsed, temp, _res1 = values[:7]
        # Anything but a full block is malformed; stop here as if the tail were torn.
        if count != SAMPLES_PER_BLOCK:
            return None
        floats = values[7:7 + SAMPLES_PER_BLOCK * FLOATS_PER_SAMPLE]
        samples: list[Sample] = [
            Sample.from_floats(floats[i * FLOATS_PER_SAMPLE:(i + 1) * FLOATS_PER_SAMPLE])
            for i in range(count)
        ]
        return cls(elapsed_ms=elapsed, samples=samples, temp_c=temp,
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

def build_filename(position: int = POS_UNSET, slot: int = 0,
                   partial: bool = False) -> str:
    """Compose the name of a recording slot.

    The name is an address on the card, not a description of what is in it.
    When the recording started, how far that time can be trusted and whether
    the tail survived all live in the header itself, where
    nothing that happens to the name can contradict them.

    The position leads because three devices share one crane: without it they
    would write the same slots and overwrite each other the moment an operator
    collected all three into one folder.
    """
    name: str = "{0}_{1:03d}{2}".format(POSITION_NAMES.get(position, "UNSET"),
                                        slot % SLOT_COUNT, EXT)
    return name + PARTIAL_SUFFIX if partial else name


def parse_filename(name: str) -> Optional[dict]:
    """Pull the position and slot back out of a name.

    Returns None when the name does not match exactly, which is also the
    path-traversal guard: no separators or dots can survive this.
    """
    m = FILENAME_RE.match(os.path.basename(name))
    if m is None:
        return None
    return {
        "position": POSITION_VALUES.get(m.group("flag").lower(), POS_UNSET),
        "position_name": m.group("flag"),
        "slot": int(m.group("slot")),
    }


def next_slot(data_dir: str) -> int:
    """The slot to record into next, counting 000..999 and then round again.

    Kept on the card rather than derived from what is there, so collecting
    files does not hand their numbers back out. Two recordings made either
    side of a collection get different names, which is what lets an operator
    put them in one folder.

    A card that cannot be read gives slot 0. That risks overwriting one
    recording; refusing to record would lose all of them.
    """
    path: str = os.path.join(data_dir, SLOT_FILE)
    previous: int = -1
    try:
        with open(path, encoding="utf-8") as f:
            previous = int(f.read().strip())
    except (OSError, ValueError):
        pass
    slot: int = (previous + 1) % SLOT_COUNT
    tmp: str = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(slot))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(data_dir)
    except OSError as exc:
        logger.error("Could not advance the slot counter: {}", exc)
    return slot


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
        self._f: BinaryIO = open(path, "w+b" if new else "r+b")
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


def start_epoch(path: str) -> float:
    """When the device believed this recording started.

    The one answer there is. It went into the header when the file was opened
    and nothing revises it: the clock may have been wrong and the device has
    no way to find out, so calling it anything more certain than "what the
    device believed" would be a claim nobody can back.

    Ordering and grouping rest on this and are sound for it -- every recording
    on a card came off the same clock, so the differences between them hold
    even where the absolute value does not.
    """
    return read_header(path).start_epoch


def was_recovered(path: str) -> bool:
    """True if this recording lost its end to a power cut."""
    return read_header(path).recovered


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------

def recover_partial(partial_path: str, corrupt_dir: Optional[str] = None,
                    mark_recovered: bool = True) -> Optional[str]:
    """Verify a leftover .partial, trim the torn tail, and finalise it.

    This is the normal end of a recording: in the field the power simply drops,
    so nothing gets finalised at shutdown (section 6). Returns the new path, or
    None if the file was unusable and got moved aside.

    Finalising is now only the loss of the .partial suffix -- the slot was
    settled when the recording opened. What recovery still decides is what the
    sidecar says: the corrected start time if one arrived, and whether the tail
    was lost.

    mark_recovered=False is for the rare orderly stop, where the tail is known
    to be complete. "recovered" means "this file lost its end", so putting it
    on a cleanly closed file would misinform the operator.
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

    if mark_recovered:
        # The one fact recovery establishes, written where it will travel with
        # the file. Safe to rewrite the header here and nowhere else: nothing
        # is reading or appending to a .partial at boot.
        header: Header = summary.header
        header.flags |= FILE_FLAG_RECOVERED
        with open(partial_path, "r+b") as f:
            f.write(header.pack())
            os.fsync(f.fileno())

    final_path: str = os.path.join(directory, base[:-len(PARTIAL_SUFFIX)])
    os.replace(partial_path, final_path)
    _fsync_dir(directory)
    logger.info("Recovered {} -> {}", base, os.path.basename(final_path))
    return final_path


def _quarantine(path: str, corrupt_dir: Optional[str]) -> None:
    if corrupt_dir is None:
        corrupt_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "corrupt")
    os.makedirs(corrupt_dir, exist_ok=True)
    target: str = os.path.join(corrupt_dir, os.path.basename(path))
    try:
        os.replace(path, target)
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
        entries.append((start_epoch(path), path, summary))
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
    base_start: float = start_epoch(paths[0])
    with open(out_path, "wb") as out:
        out.write(read_header(paths[0]).pack())
        for path in paths:
            offset_ms: int = int(round((start_epoch(path) - base_start) * 1000.0))
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
