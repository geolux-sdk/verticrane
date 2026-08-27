# coding:UTF-8
# The recording directory as the operator sees it: what exists, what belongs
# together, and what has already been collected (sections 5.4 and 7).
#
# Kept out of the web layer because the recorder needs the housekeeping too --
# it purges the trash when the card runs low, and nothing about that needs Flask.

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

import ahrs_file as af

TRASH_DIR: str = "trash"
CORRUPT_DIR: str = "corrupt"


@dataclass
class FileInfo:
    name: str
    path: str
    size: int
    start_epoch: float
    duration_s: float
    samples: int
    blocks: int
    trusted: bool                  # False means the timestamp is a guess
    recovered: bool                # True means the tail was lost to a power cut
    group: int = 0

    @property
    def end_epoch(self) -> float:
        return self.start_epoch + self.duration_s

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "start_epoch": round(self.start_epoch, 3),
            "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.start_epoch)),
            "duration_s": round(self.duration_s, 1),
            "samples": self.samples,
            "blocks": self.blocks,
            "trusted_time": self.trusted,
            "recovered": self.recovered,
            "group": self.group,
        }


def quick_summary(path: str) -> Optional[tuple[int, int, float]]:
    """(blocks, samples, duration_s) without reading the whole file.

    Every block is full except possibly the last one, so the count follows from
    the file size and only the final block has to be read. A 100 MB recording
    would otherwise take a full scan just to be listed.
    """
    try:
        size: int = os.path.getsize(path)
        header = af.read_header(path)
    except (OSError, af.FormatError):
        return None
    body: int = size - af.HEADER_SIZE
    if body <= 0 or body % af.BLOCK_SIZE:
        return None                      # ragged: fall back to a full scan
    blocks: int = body // af.BLOCK_SIZE
    try:
        with open(path, "rb") as f:
            f.seek(af.HEADER_SIZE + (blocks - 1) * af.BLOCK_SIZE)
            last = af.Block.unpack(f.read(af.BLOCK_SIZE))
    except OSError:
        return None
    if last is None:
        return None
    samples: int = (blocks - 1) * af.SAMPLES_PER_BLOCK + last.count
    duration: float = (last.elapsed_ms + last.duration_ms) / 1000.0
    _ = header
    return blocks, samples, duration


def describe(path: str) -> Optional[FileInfo]:
    summary: Optional[tuple[int, int, float]] = quick_summary(path)
    if summary is None:
        try:
            scanned = af.scan(path)
            summary = (scanned.blocks, scanned.samples, scanned.duration_s)
        except (OSError, af.FormatError) as exc:
            logger.warning("Skipping {}: {}", os.path.basename(path), exc)
            return None
    blocks, samples, duration = summary
    if blocks == 0:
        return None
    parsed: Optional[dict] = af.parse_filename(path)
    start, _quality = af.effective_start(path)
    return FileInfo(
        name=os.path.basename(path),
        path=path,
        size=os.path.getsize(path),
        start_epoch=start,
        duration_s=duration,
        samples=samples,
        blocks=blocks,
        trusted=bool(parsed["trusted"]) if parsed else False,
        recovered=bool(parsed["recovered"]) if parsed else False,
    )


def list_files(data_dir: str, gap_tolerance_s: float = 2.0) -> list[FileInfo]:
    """Finalised recordings, newest first, tagged with their continuity group.

    A .partial is deliberately absent: it has not been verified yet, so handing
    it to the operator would mean handing over a file whose end may be missing
    with no way to tell (section 6).
    """
    infos: list[FileInfo] = []
    try:
        names: list[str] = os.listdir(data_dir)
    except OSError as exc:
        logger.error("Cannot read {}: {}", data_dir, exc)
        return []

    for name in names:
        if not name.endswith(af.EXT) or af.parse_filename(name) is None:
            continue
        info: Optional[FileInfo] = describe(os.path.join(data_dir, name))
        if info is not None:
            infos.append(info)

    infos.sort(key=lambda i: i.start_epoch)
    group: int = 0
    for idx, info in enumerate(infos):
        if idx:
            prev: FileInfo = infos[idx - 1]
            # One sample period is the expected seam between two segments of the
            # same run; anything wider means the recording actually stopped.
            if abs(info.start_epoch - prev.end_epoch) > gap_tolerance_s:
                group += 1
        info.group = group
    infos.reverse()
    return infos


def group_members(data_dir: str, group: int, gap_tolerance_s: float = 2.0) -> list[FileInfo]:
    members: list[FileInfo] = [i for i in list_files(data_dir, gap_tolerance_s) if i.group == group]
    members.sort(key=lambda i: i.start_epoch)
    return members


# --------------------------------------------------------------------------
# Trash
# --------------------------------------------------------------------------

def trash_path(data_dir: str) -> str:
    return os.path.join(data_dir, TRASH_DIR)


def move_to_trash(data_dir: str, names: list[str]) -> list[str]:
    """Retire files the operator has downloaded (section 7).

    Not a delete: the vehicle's WiFi drops as it moves off, so a transfer that
    looked complete is worth keeping around for a few days. The operator's list
    is clean either way.
    """
    target: str = trash_path(data_dir)
    os.makedirs(target, exist_ok=True)
    moved: list[str] = []
    for name in names:
        src: Optional[str] = af.safe_join(data_dir, name)
        if src is None or not os.path.exists(src):
            continue
        dest: str = os.path.join(target, name)
        try:
            os.replace(src, dest)
            sidecar: str = af.timeinfo_path(src)
            if os.path.exists(sidecar):
                os.replace(sidecar, af.timeinfo_path(dest))
            moved.append(name)
            logger.info("Downloaded, moved to trash: {}", name)
        except OSError as exc:
            logger.error("Could not trash {}: {}", name, exc)
    return moved


def purge_trash(data_dir: str, retention_days: float = 7.0,
                free_bytes_needed: int = 0) -> int:
    """Delete trashed files past their retention, oldest first.

    free_bytes_needed lets the recorder empty the trash early when the card is
    filling up: better to drop already-collected data than to stop recording.
    """
    target: str = trash_path(data_dir)
    if not os.path.isdir(target):
        return 0
    entries: list[tuple[float, str]] = []
    for name in os.listdir(target):
        path: str = os.path.join(target, name)
        if name.endswith(af.TIMEINFO_SUFFIX) or not os.path.isfile(path):
            continue
        try:
            entries.append((os.path.getmtime(path), path))
        except OSError:
            continue
    entries.sort()

    cutoff: float = time.time() - retention_days * 86400.0
    freed: int = 0
    removed: int = 0
    for mtime, path in entries:
        expired: bool = mtime < cutoff
        needed: bool = freed < free_bytes_needed
        if not (expired or needed):
            continue
        try:
            size: int = os.path.getsize(path)
            os.remove(path)
            sidecar: str = af.timeinfo_path(path)
            if os.path.exists(sidecar):
                os.remove(sidecar)
            freed += size
            removed += 1
            logger.info("Purged from trash: {} ({})", os.path.basename(path),
                        "expired" if expired else "space needed")
        except OSError as exc:
            logger.error("Could not purge {}: {}", path, exc)
    return removed


def free_mb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / (1024.0 * 1024.0)
    except OSError:
        return 0.0


def stats(data_dir: str) -> dict[str, Any]:
    infos: list[FileInfo] = list_files(data_dir)
    trash: str = trash_path(data_dir)
    trashed: int = 0
    if os.path.isdir(trash):
        trashed = sum(1 for n in os.listdir(trash) if n.endswith(af.EXT))
    return {
        "files": len(infos),
        "groups": len({i.group for i in infos}),
        "bytes": sum(i.size for i in infos),
        "trashed": trashed,
        "free_mb": round(free_mb(data_dir), 1),
    }
