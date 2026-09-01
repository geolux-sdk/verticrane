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
    recovered: bool                # True means the tail was lost to a power cut
    position: str = "UNSET"        # BASE / MIDDLE / TOP
    slot: int = 0                  # the rotating number in the name
    group: int = 0

    @property
    def end_epoch(self) -> float:
        return self.start_epoch + self.duration_s

    @property
    def start(self) -> str:
        """The start as the operator reads it.

        A property, not something as_dict() alone knows how to render: the file
        list asks for it directly, and a name is a rotating slot number, so this
        is the only thing on the row that tells two recordings apart. Missing,
        it rendered as an empty string and said nothing at all.
        """
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.start_epoch))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "start_epoch": round(self.start_epoch, 3),
            "start": self.start,
            "duration_s": round(self.duration_s, 1),
            "samples": self.samples,
            "blocks": self.blocks,
            "recovered": self.recovered,
            "position": self.position,
            "slot": self.slot,
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
    # Inside the guard with the rest: a file can be retired or purged between
    # the listing that named it and the read that describes it, and when that
    # happens the answer is one entry missing from the list, not a page that
    # will not render at all.
    try:
        header: af.Header = af.read_header(path)
        size: int = os.path.getsize(path)
    except (OSError, af.FormatError) as exc:
        logger.warning("Skipping {}: {}", os.path.basename(path), exc)
        return None
    return FileInfo(
        name=os.path.basename(path),
        path=path,
        size=size,
        start_epoch=header.start_epoch,
        duration_s=duration,
        samples=samples,
        blocks=blocks,
        recovered=header.recovered,
        position=str(parsed["position_name"]) if parsed else "UNSET",
        slot=int(parsed["slot"]) if parsed else 0,
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


def group_containing(data_dir: str, name: str,
                     gap_tolerance_s: float = 2.0) -> list[FileInfo]:
    """The members of the group that `name` belongs to, oldest first.

    Addressed by a member's name rather than by the group number, because the
    number is positional: it is handed out by counting from the oldest file on
    the card every time the list is built, so retiring one group renumbers all
    the ones behind it. A link the operator is looking at then points at data
    it was not drawn for.

    A name cannot drift like that. If the file is gone the answer is nothing,
    which the caller turns into a 404 -- an honest one, where the number would
    have quietly served a different measurement.
    """
    infos: list[FileInfo] = list_files(data_dir, gap_tolerance_s)
    anchor: Optional[FileInfo] = next((i for i in infos if i.name == name), None)
    if anchor is None:
        return []
    members: list[FileInfo] = [i for i in infos if i.group == anchor.group]
    members.sort(key=lambda i: i.start_epoch)
    return members


def group_anchors(infos: list[FileInfo]) -> list[tuple[FileInfo, int]]:
    """(oldest member, member count) per group, for the merged-download links.

    The oldest is the anchor because it is the one whose name the merged file
    already carries (section 5.4), so the link and the file the operator ends
    up with agree.
    """
    oldest: dict[int, FileInfo] = {}
    counts: dict[int, int] = {}
    for info in infos:
        counts[info.group] = counts.get(info.group, 0) + 1
        if info.group not in oldest or info.start_epoch < oldest[info.group].start_epoch:
            oldest[info.group] = info
    return [(oldest[g], counts[g]) for g in sorted(oldest)]


# --------------------------------------------------------------------------
# Trash
# --------------------------------------------------------------------------

def trash_path(data_dir: str) -> str:
    return os.path.join(data_dir, TRASH_DIR)


def locate(data_dir: str, name: str) -> Optional[str]:
    """Where a recording is right now: still to be collected, or already retired.

    The download and the request that retires it come from one click and arrive
    together, so by the time the bytes are asked for the file may have moved.
    The operator asked for a recording by name, not for a place on the card,
    and the answer is the same bytes wherever it currently sits.
    """
    path: Optional[str] = af.safe_join(data_dir, name)
    if path is None:
        return None
    if os.path.exists(path):
        return path
    retired: str = os.path.join(trash_path(data_dir), os.path.basename(path))
    return retired if os.path.exists(retired) else None


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
        if not os.path.isfile(path):
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
            freed += size
            removed += 1
            logger.info("Purged from trash: {} ({})", os.path.basename(path),
                        "expired" if expired else "space needed")
        except OSError as exc:
            logger.error("Could not purge {}: {}", path, exc)
    return removed


# --------------------------------------------------------------------------
# Browsing the card
# --------------------------------------------------------------------------
#
# The file list answers "what is there to collect", which is the right question
# nearly always and the wrong one exactly when something has gone sideways: a
# recording discarded because someone connected, one quarantined at boot, a log
# from a tool that predates all of this. Those are on the card and nowhere on
# any page. This is the way to see them, and it only reads.

@dataclass
class Entry:
    name: str
    rel: str                       # path relative to the data directory
    is_dir: bool
    size: int
    mtime: float

    @property
    def modified(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.mtime))

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "rel": self.rel, "is_dir": self.is_dir,
                "size": self.size, "mtime": round(self.mtime, 3),
                "modified": self.modified}


def resolve_within(root: str, rel: str) -> Optional[str]:
    """Resolve `rel` under `root`, or None if it lands anywhere else.

    Deliberately not af.safe_join. That one refuses any name carrying a
    separator at all, and browsing has to walk into trash/ and corrupt/ -- but
    it is also the guard standing in front of the download the operator uses
    every day, and loosening a shared guard to serve a new page is how the hole
    arrives two refactors later. So this is a second, separate resolver and
    af.safe_join keeps its strictness.

    The containment test runs after realpath, which is the part that matters:
    checked before, a symlink inside the directory could still step out of it.
    """
    root_real: str = os.path.realpath(root)
    candidate: str = os.path.realpath(os.path.join(root_real, rel or ""))
    try:
        if os.path.commonpath([root_real, candidate]) != root_real:
            return None
    except ValueError:
        return None                # different drives on Windows: not under root
    return candidate


def list_dir(root: str, rel: str = "") -> Optional[tuple[str, list[Entry]]]:
    """(normalised rel, entries) for one directory, or None if it is not there.

    Directories first, then newest first: what someone comes here for is
    usually either a subdirectory they were told about or the most recent
    thing on the card.
    """
    path: Optional[str] = resolve_within(root, rel)
    if path is None or not os.path.isdir(path):
        return None
    root_real: str = os.path.realpath(root)
    normalised: str = os.path.relpath(path, root_real).replace(os.sep, "/")
    if normalised == ".":
        normalised = ""

    entries: list[Entry] = []
    try:
        names: list[str] = os.listdir(path)
    except OSError as exc:
        logger.error("Cannot read {}: {}", path, exc)
        return normalised, []

    for name in names:
        full: str = os.path.join(path, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue               # vanished between the listing and here
        entries.append(Entry(
            name=name,
            rel="{0}/{1}".format(normalised, name) if normalised else name,
            is_dir=os.path.isdir(full),
            size=0 if os.path.isdir(full) else stat.st_size,
            mtime=stat.st_mtime,
        ))
    entries.sort(key=lambda e: (not e.is_dir, -e.mtime))
    return normalised, entries


def parent_of(rel: str) -> Optional[str]:
    """The directory above `rel`, or None at the top of the card."""
    if not rel:
        return None
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def free_mb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / (1024.0 * 1024.0)
    except OSError:
        return 0.0


def stats(data_dir: str) -> dict[str, Any]:
    infos: list[FileInfo] = list_files(data_dir)
    return {
        "files": len(infos),
        "groups": len({i.group for i in infos}),
        "bytes": sum(i.size for i in infos),
        "trashed": _count_in(trash_path(data_dir)),
        # Counted for the same reason as the trash: when the list is empty the
        # operator needs to know whether that means there is nothing, or that
        # everything ended up somewhere they were never shown.
        "corrupt": _count_in(os.path.join(data_dir, CORRUPT_DIR)),
        "free_mb": round(free_mb(data_dir), 1),
    }


def _count_in(directory: str) -> int:
    """Recordings sitting in one of the side directories.

    A quarantined file keeps its .partial suffix, so match on the extension
    appearing anywhere in the name rather than at the end of it.
    """
    if not os.path.isdir(directory):
        return 0
    try:
        return sum(1 for n in os.listdir(directory) if af.EXT in n)
    except OSError:
        return 0
