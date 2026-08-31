#!/usr/bin/env python3
# coding:UTF-8
# Windows 실행 파일용 런처 — 웹 자산을 localhost 로 서빙하고 브라우저를 띄운다.
#
#   python launcher.py            # 소스에서 그대로 실행 (개발)
#   VerticraneBT.exe              # build_exe.py 로 만든 실행 파일
#
# serve.py 와 무엇이 다른가:
#
#   serve.py 는 개발자가 쓰는 정적 서버다. 파이썬이 깔린 PC 에서 폴더를 열고
#   포트를 정해 띄우고, 브라우저 주소를 직접 친다. 현장에 나가는 사람에게
#   시킬 일이 아니다.
#
#   이 런처는 그 세 가지를 없앤다. 자산을 실행 파일 안에 넣고(파이썬 불필요),
#   Web Bluetooth 가 동작하는 브라우저를 직접 찾아 띄우고, 그 창이 닫히면
#   서버도 같이 내린다. 더블클릭 한 번과 창 닫기 한 번이 전부다.
#
# 왜 브라우저를 골라서 여는가: `webbrowser.open` 은 기본 브라우저를 연다.
# 그게 Firefox 면 Web Bluetooth 가 아예 없어 앱이 첫 화면에서 멈춘다. 그래서
# Edge/Chrome 을 직접 찾아 실행한다 (Windows 11 이면 Edge 는 항상 있다).
#
# 왜 전용 프로필을 쓰는가: bt/web/README.md 가 적어 둔 두 플래그
# (getDevices / watchAdvertisements) 없이는 장비 목록을 만들 수 없다. 그런데
# 이미 떠 있는 브라우저에 명령줄 플래그를 주면 기존 프로세스에 합류하면서
# 조용히 무시된다. --user-data-dir 로 이 앱만의 프로필을 쓰면 플래그가 실제로
# 적용되고, 사용자의 평소 브라우저 설정을 건드리지 않으며, 한 번 허용한 BLE
# 장비 권한이 그 프로필에 남아 다음 실행에도 목록에 뜬다.
#
# 왜 포트를 고정하는가: 브라우저의 장비 권한과 localStorage 는 출처(origin)에
# 묶인다. 포트가 바뀌면 같은 앱이라도 다른 출처가 되어 기억된 장비가 전부
# 사라진다. 8000 은 다른 개발 서버와 자주 겹치므로 겹칠 일이 드문 번호를 쓴다.

from __future__ import annotations

import ctypes
import functools
import http.server
import mimetypes
import os
import subprocess
import sys
import threading
import time
from typing import Optional

APP_NAME = "VerticraneBT"
PORT = 8747
HOST = "127.0.0.1"
# 겹쳤을 때만 위로 옮긴다. 옮기면 그 실행에 한해 기억된 장비가 보이지 않는다.
PORT_FALLBACKS = 8


def app_dir() -> str:
    """프로필과 로그를 두는 곳. 실행 파일 옆이 아니라 사용자 폴더다 —
    실행 파일은 읽기 전용 위치(Program Files, 네트워크 드라이브, USB)에
    놓일 수 있다."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def web_root() -> str:
    """서빙할 자산의 위치. 실행 파일이면 PyInstaller 가 풀어 놓은 임시 폴더,
    아니면 이 스크립트 옆이다."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "web")  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


_log_file = None


def log(msg: str) -> None:
    global _log_file
    line = time.strftime("%Y-%m-%d %H:%M:%S  ") + msg
    if _log_file is not None:
        try:
            _log_file.write(line + "\n")
            _log_file.flush()
        except Exception:
            pass


def open_log() -> None:
    """창 없는 실행 파일에서는 sys.stdout 이 None 이라 print 한 번에 죽는다.
    표준 출력 자체를 로그 파일로 돌려 그 사고를 없앤다."""
    global _log_file
    path = os.path.join(app_dir(), "launcher.log")
    try:
        # 매 실행마다 새로 쓴다. 문제를 물어볼 때 필요한 것은 직전 실행뿐이고,
        # 무한히 자라는 로그는 아무도 지우지 않는다.
        _log_file = open(path, "w", encoding="utf-8")
    except Exception:
        return
    if getattr(sys, "frozen", False):
        sys.stdout = _log_file
        sys.stderr = _log_file


def die(msg: str) -> None:
    """치명적 오류. 창 없는 실행 파일이므로 대화상자로 알린다 —
    아무 일도 일어나지 않는 것처럼 보이는 것이 가장 나쁘다."""
    log("FATAL: " + msg)
    detail = msg + f"\n\n자세한 내용: {os.path.join(app_dir(), 'launcher.log')}"
    try:
        ctypes.windll.user32.MessageBoxW(None, detail, APP_NAME + " — 실행할 수 없습니다", 0x10)
    except Exception:
        pass
    sys.exit(1)


# --- 브라우저 찾기 -----------------------------------------------------------

def _registry_path(exe: str) -> Optional[str]:
    """App Paths 는 설치된 위치를 그대로 알려준다. 사용자가 기본 경로가 아닌
    곳에 깔았거나 회사 이미지가 다른 드라이브를 쓰는 경우를 잡는다."""
    try:
        import winreg
    except ImportError:
        return None
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as k:
                path, _ = winreg.QueryValueEx(k, "")
                if path and os.path.isfile(path):
                    return path
        except OSError:
            continue
    return None


def find_browser() -> tuple[str, str]:
    """(경로, 표시이름). Edge 를 먼저 보는 이유는 Windows 11 에 항상 있기
    때문이지 더 낫기 때문이 아니다 — 둘 다 같은 엔진이다."""
    candidates = [
        ("msedge.exe", "Edge", [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]),
        ("chrome.exe", "Chrome", [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]),
    ]
    for exe, label, paths in candidates:
        found = _registry_path(exe)
        if found:
            return found, label
        for p in paths:
            if os.path.isfile(p):
                return p, label
    die("Edge 또는 Chrome 을 찾지 못했습니다.\n\n"
        "Web Bluetooth 는 Chromium 계열 브라우저에서만 동작합니다. "
        "Firefox 나 Safari 로는 장비에 연결할 수 없습니다.")
    raise AssertionError("unreachable")


def launch_browser(exe: str, url: str) -> subprocess.Popen:
    profile = os.path.join(app_dir(), "browser")
    args = [
        exe,
        f"--app={url}",
        f"--user-data-dir={profile}",
        # 전용 프로필이라 첫 실행 안내와 기본 브라우저 질문이 매번 뜬다. 끈다.
        "--no-first-run",
        "--no-default-browser-check",
        # 이 두 개가 장비 목록을 만드는 기능이다 (bt/web/README.md 참조).
        # 사용자가 chrome://flags 를 뒤지지 않아도 되도록 여기서 켠다.
        "--enable-features=WebBluetoothNewPermissionsBackend",
        "--enable-experimental-web-platform-features",
    ]
    log("browser: " + " ".join(args))
    # CREATE_NEW_PROCESS_GROUP: 콘솔에서 실행했을 때 Ctrl+C 가 브라우저까지
    # 내려가 창이 먼저 죽는 것을 막는다.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(args, creationflags=flags)


# --- 서버 -------------------------------------------------------------------

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *a) -> None:  # noqa: A003
        log("http  " + (fmt % a))

    def end_headers(self) -> None:
        # 서비스워커가 캐시한 낡은 셸이 아니라 실행 파일 안의 것을 보게 한다.
        # 실행 파일을 새로 받았는데 예전 화면이 뜨는 사고를 막는다.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def bind_server(root: str) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(Handler, directory=root)
    last: Optional[OSError] = None
    for port in range(PORT, PORT + PORT_FALLBACKS + 1):
        try:
            srv = http.server.ThreadingHTTPServer((HOST, port), handler)
        except OSError as e:
            last = e
            log(f"port {port} busy: {e}")
            continue
        srv.daemon_threads = True
        if port != PORT:
            log(f"NOTE: {PORT} 이 사용 중이라 {port} 로 열었습니다. "
                "이번 실행에서는 기억된 장비 목록이 비어 보입니다.")
        return srv
    die(f"{PORT}–{PORT + PORT_FALLBACKS} 포트를 모두 열 수 없습니다.\n"
        f"({last})")
    raise AssertionError("unreachable")


def main() -> int:
    open_log()
    root = web_root()
    log(f"{APP_NAME} start | root={root} | frozen={getattr(sys, 'frozen', False)}")

    if not os.path.isfile(os.path.join(root, "index.html")):
        die(f"웹 자산을 찾을 수 없습니다: {root}")

    # 파이썬 기본 테이블에 없어 application/octet-stream 으로 나가는 것들.
    mimetypes.add_type("font/woff2", ".woff2")
    mimetypes.add_type("application/manifest+json", ".webmanifest")

    srv = bind_server(root)
    port = srv.server_address[1]
    # localhost 가 아니라 127.0.0.1 이다. PC 에 따라 localhost 는 ::1 로 먼저
    # 풀리는데 서버는 IPv4 loopback 에만 붙어 있어(그래야 바깥에 열리지 않는다)
    # 브라우저가 한 번 거절당한 뒤 되물어야 한다. 주소를 못 박으면 그 왕복이
    # 없어진다. Web Bluetooth 와 서비스워커가 요구하는 보안 컨텍스트는
    # 127.0.0.0/8 도 localhost 와 똑같이 인정한다.
    url = f"http://{HOST}:{port}/index.html"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("serving " + url)

    exe, label = find_browser()
    log(f"browser found: {label} at {exe}")

    # 브라우저가 첫 요청을 넣기 전에 서버가 떠 있어야 한다. 위에서 이미 bind
    # 까지 끝냈으므로 연결은 성립한다 — 여기서 기다릴 것은 없다.
    proc = launch_browser(exe, url)

    # 앱 창이 이 앱의 수명이다. 창을 닫으면 서버도 내려간다. 창 없는 실행
    # 파일에서 서버만 남아 도는 것이 이 방식으로 막힌다.
    try:
        proc.wait()
    except KeyboardInterrupt:
        log("interrupted")
        try:
            proc.terminate()
        except Exception:
            pass
    log("browser closed | shutting down")
    srv.shutdown()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        import traceback
        log(traceback.format_exc())
        die(f"예상치 못한 오류: {exc}")
