#!/usr/bin/env python3
# coding:UTF-8
# launcher.py + 웹 자산을 파일 하나짜리 Windows 실행 파일로 굽는다.
#
#   python build_exe.py              # dist/VerticraneBT.exe
#   python build_exe.py --clean      # build/ dist/ 를 지우고 처음부터
#
# 준비물은 PyInstaller 하나뿐이다 (`pip install pyinstaller`). Pillow 가 있으면
# 앱 아이콘까지 굽는다 — 없으면 기본 아이콘으로 넘어간다.
#
# 실행 파일은 개발 PC에서 한 번 구우면 파이썬이 없는 노트북에 그대로 복사해
# 쓸 수 있다. 현장에 나가는 노트북에 파이썬을 깔지 않아도 되는 것이 이 파일이
# 존재하는 이유다.

from __future__ import annotations

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "VerticraneBT"

# 실행 파일 안에 넣을 것들. tests/ 와 serve.py, 이 스크립트는 넣지 않는다 —
# 브라우저가 요청하지 않는 파일이고, 넣으면 실행 파일만 커진다.
ASSETS = ["index.html", "manifest.json", "sw.js", "icons"]


def make_icon() -> str | None:
    """PNG 아이콘을 .ico 로 바꾼다. Windows 는 실행 파일 아이콘으로 .ico 만
    받는다. Pillow 가 없으면 조용히 포기한다 — 아이콘 때문에 빌드가 실패할
    이유는 없다."""
    src = os.path.join(HERE, "icons", "icon-512.png")
    dst = os.path.join(HERE, "icons", "app.ico")
    if not os.path.isfile(src):
        return None
    try:
        from PIL import Image
    except ImportError:
        print("  (Pillow 없음 — 기본 아이콘을 씁니다: pip install pillow)")
        return None
    img = Image.open(src).convert("RGBA")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(dst, format="ICO", sizes=sizes)
    print(f"  아이콘: {dst}")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="build/ dist/ 를 먼저 지운다")
    args = ap.parse_args()

    if os.name != "nt":
        print("경고: Windows 실행 파일은 Windows 에서만 구울 수 있습니다.", file=sys.stderr)

    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("PyInstaller 가 없습니다.  pip install pyinstaller", file=sys.stderr)
        return 1

    for d in ("build", "dist"):
        p = os.path.join(HERE, d)
        if args.clean and os.path.isdir(p):
            shutil.rmtree(p)
            print(f"  지움: {p}")

    icon = make_icon()

    opts = [
        os.path.join(HERE, "launcher.py"),
        "--name", NAME,
        # 파일 하나로. 현장 노트북에 폴더째 복사하다 일부가 빠지는 사고를 없앤다.
        "--onefile",
        # 콘솔 창 없이. 이 앱의 화면은 브라우저 창이고, 검은 창이 하나 더 뜨면
        # 사용자는 그것을 닫아야 할지 말아야 할지 알 수 없다. 대신 오류는
        # 대화상자로 알리고 자세한 것은 로그 파일에 남긴다 (launcher.py 참조).
        "--windowed",
        "--noconfirm",
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", os.path.join(HERE, "build"),
    ]
    for a in ASSETS:
        src = os.path.join(HERE, a)
        if not os.path.exists(src):
            print(f"경고: 없어서 건너뜁니다 — {a}", file=sys.stderr)
            continue
        # 실행 파일 안에서 web/ 아래에 놓인다 (launcher.web_root 와 짝).
        dest = "web" if os.path.isfile(src) else os.path.join("web", a)
        opts += ["--add-data", f"{src}{os.pathsep}{dest}"]
    if icon:
        opts += ["--icon", icon]

    print("PyInstaller 실행:\n  " + " ".join(opts) + "\n")
    pyi.run(opts)

    out = os.path.join(HERE, "dist", NAME + ".exe")
    if os.path.isfile(out):
        mb = os.path.getsize(out) / (1024 * 1024)
        print(f"\n완료: {out}  ({mb:.1f} MB)")
        return 0
    print("\n실행 파일이 생기지 않았습니다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
