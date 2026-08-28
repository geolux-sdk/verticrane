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


def download(base: str, name: str, dest: str) -> int:
    """Stream one recording to disk. Returns the bytes written.

    Written to a .part and renamed only once the whole body has arrived and
    reached the disk. An interrupted transfer therefore leaves nothing that
    looks like a finished file, which matters because the next thing this
    script does is tell the device it may retire the original.
    """
    tmp = dest + ".part"
    url = base + "/api/files/" + urllib.parse.quote(name)
    written = 0
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        declared = int(r.headers.get("Content-Length") or 0)
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
            f.flush()
            os.fsync(f.fileno())
    if declared and written != declared:
        os.unlink(tmp)
        raise IOError("{0}: got {1} of {2} bytes".format(name, written, declared))
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
            size = download(base, name, dest)
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
