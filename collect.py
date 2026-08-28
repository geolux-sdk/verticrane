# coding:UTF-8
# Collect every recording off one or more devices, in one command.
#
#   python collect.py 192.168.0.19
#   python collect.py 192.168.0.19 192.168.0.20 192.168.0.21 --out ./2026-08-28
#   python collect.py 192.168.0.19 --keep        # download but do not retire
#
# Runs on the operator's laptop, not on the device. Standard library only, so
# there is nothing to install on a machine that is about to be driven to a site.
#
# Connecting ends a measurement in progress -- that is the device's rule, not
# this script's (section 3): reaching it means someone is beside it, and the
# recording that was running only started because nobody was. A person runs
# this, so that is exactly right, and the file is finalised into the trash
# rather than lost. It is announced anyway, because a surprise is worse than a
# rule.

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

TIMEOUT = 30.0
CHUNK = 256 * 1024


def _get(base: str, path: str, timeout: float = TIMEOUT) -> Any:
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(base: str, path: str, timeout: float = TIMEOUT) -> Any:
    req = urllib.request.Request(base + path, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body else {}


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "{0:.1f} {1}".format(n, unit)
        n /= 1024.0
    return ""


def download(base: str, name: str, dest: str, expect: int,
             attempts: int = 4) -> int:
    """Stream one recording to disk, resuming where it left off.

    Written to a .part and renamed only once the whole file has arrived and
    reached the disk. An interrupted transfer therefore leaves nothing that
    looks like a finished file, which matters because the next thing this
    script does is tell the device it may retire the original -- and the .part
    that is left is exactly what the next attempt continues from.

    Resuming is not a refinement here. The vehicle's WiFi drops as it moves
    off, so losing a transfer most of the way through a 100 MB file is the
    normal case, and starting again from zero can mean never finishing.
    """
    tmp = dest + ".part"
    url = base + "/api/files/" + urllib.parse.quote(name)

    for attempt in range(attempts):
        have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        if have >= expect > 0:
            break
        req = urllib.request.Request(url)
        if have:
            req.add_header("Range", "bytes={0}-".format(have))
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                # A device that ignored the Range gives 200 and the whole body;
                # start over rather than append it onto what is already there.
                if have and r.status != 206:
                    have = 0
                mode = "r+b" if have else "wb"
                with open(tmp, mode) as f:
                    if have:
                        f.seek(have)
                    while True:
                        chunk = r.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
        except (urllib.error.URLError, OSError) as exc:
            if attempt == attempts - 1:
                raise IOError("{0}: {1}".format(name, exc))
            print("       … 끊겼습니다. 이어받습니다 ({0}/{1})".format(
                attempt + 2, attempts))
            continue

    written = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    if expect and written != expect:
        raise IOError("{0}: got {1} of {2} bytes".format(name, written, expect))
    os.replace(tmp, dest)
    return written


def collect(host: str, out_dir: str, keep: bool) -> tuple[int, int, int]:
    """Take everything one device is holding. Returns (files, bytes, failures)."""
    base = host if host.startswith("http") else "http://{0}:8080".format(host)

    try:
        status = _get(base, "/api/status?auto=1", timeout=10.0)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("  X  연결할 수 없습니다: {0}".format(exc))
        return 0, 0, 1

    device = status.get("sensor_id") or host
    position = status.get("position") or "UNSET"
    print("  {0} [{1}]  상태 {2}".format(device, position, status.get("state")))
    if status.get("state") == "recording":
        print("     기록 중이던 파일은 휴지통으로 들어갑니다 (§3). 이 목록에는 없습니다.")

    try:
        listing = _get(base, "/api/files")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("  X  목록을 받지 못했습니다: {0}".format(exc))
        return 0, 0, 1

    files = listing.get("files", [])
    if not files:
        print("     받을 파일이 없습니다.")
        return 0, 0, 0

    room = os.path.join(out_dir, device)
    os.makedirs(room, exist_ok=True)

    got = total = failed = 0
    for info in files:
        name = info["name"]
        dest = os.path.join(room, name)
        if os.path.exists(dest) and os.path.getsize(dest) == info["size"]:
            print("     = {0:<16} 이미 있음".format(name))
            continue
        try:
            started = time.monotonic()
            size = download(base, name, dest, info["size"])
            took = max(time.monotonic() - started, 0.001)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print("     X {0:<16} {1}".format(name, exc))
            failed += 1
            continue

        # Only now: the bytes are on this disk and the size matches what the
        # device said. The device cannot observe delivery itself -- a finished
        # response only means the bytes left its socket -- so this call is the
        # only thing that retires a recording (section 7).
        retired = ""
        if not keep:
            try:
                _post(base, "/api/files/" + urllib.parse.quote(name) + "/collected")
                retired = " → 휴지통"
            except (urllib.error.URLError, OSError, ValueError) as exc:
                retired = " (확인 실패: {0})".format(exc)
        print("     + {0:<16} {1:>10}  {2:>7}/s{3}".format(
            name, _human(size), _human(size / took), retired))
        got += 1
        total += size

    return got, total, failed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Verticrane 장비에서 기록 파일을 모두 받아옵니다.")
    ap.add_argument("hosts", nargs="+", metavar="HOST",
                    help="장비 주소. 예: 192.168.0.19")
    ap.add_argument("--out", default="verticrane-data",
                    help="받을 폴더 (기본: ./verticrane-data). 장비별로 하위 폴더가 생깁니다")
    ap.add_argument("--keep", action="store_true",
                    help="받기만 하고 장비에서 은퇴시키지 않습니다")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    files = total = failed = 0
    for host in args.hosts:
        print("\n{0}".format(host))
        f, b, x = collect(host, args.out, args.keep)
        files, total, failed = files + f, total + b, failed + x

    print("\n{0}개 · {1} → {2}{3}".format(
        files, _human(total), os.path.abspath(args.out),
        "   실패 {0}".format(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
