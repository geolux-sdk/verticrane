# coding:UTF-8
# Self-check for the panel's two decisions: which screen a moment calls for,
# and when the thread should next wake up (section 9).
#
#   python test_eink_panel.py
#
# No hardware and no recorder: both decisions are pure functions of a status
# snapshot, which is what makes them worth pinning down here. The frames
# themselves are reviewed by eye with `python eink_panel.py --out panel.png`.

from __future__ import annotations

import sys
import time
from typing import Any, Optional

import eink_panel as ep

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", label,
                                 "" if ok else "  <- " + detail))
    if not ok:
        _failures.append(label)


def test_screens() -> None:
    print("\n[1] 상태 -> 화면")
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("부팅 중에는 무엇보다 부팅 화면",
         {"booting": True, "state": "recording", "elapsed_s": 9999}, ep.SCREEN_BOOT),
        ("접속 대기는 설치 안내", {"state": "waiting_http"}, ep.SCREEN_INSTALL),
        ("안정 대기도 설치 안내", {"state": "waiting_stable"}, ep.SCREEN_INSTALL),
        ("조작자가 붙으면 브랜드", {"state": "maintenance"}, ep.SCREEN_BRAND),
        ("기록 시작 직후는 기록 화면",
         {"state": "recording", "elapsed_s": 0}, ep.SCREEN_RECORDING),
        ("전환 직전까지 기록 화면",
         {"state": "recording", "elapsed_s": ep.MEASURE_AFTER_S - 0.1},
         ep.SCREEN_RECORDING),
        ("전환 시각에 측정 화면",
         {"state": "recording", "elapsed_s": ep.MEASURE_AFTER_S}, ep.SCREEN_MEASURE),
    ]
    for label, status, want in cases:
        got: str = ep.pick_screen(status)
        check(label, got == want, "{0} != {1}".format(got, want))


class _Recorder:
    """Just enough of a recorder for PanelThread to read a snapshot."""

    class _Snap:
        sensor_id = "pi-test"
        position = "TOP"
        state = "maintenance"
        tilt = None
        temp_c = None
        samples = 0
        elapsed_s = 0.0

    def snapshot(self) -> Any:
        return self._Snap()


def test_wakeups() -> None:
    """The panel is event-driven, so what it does *not* schedule matters most.

    A returning None here is the whole design: the thread blocks until someone
    calls refresh_now(), and an untouched device spends no refreshes at all.
    """
    print("\n[2] 다음 갱신 예약")
    panel = ep.PanelThread(_Recorder(), {})

    panel._started = time.monotonic()
    splash: Optional[float] = panel._next_wait({"booting": True})
    check("부팅 화면은 스플래시가 끝날 때 깨어난다",
          splash is not None and 0 < splash <= ep.BOOT_SPLASH_S, repr(splash))

    panel._started = time.monotonic() - (ep.BOOT_SPLASH_S + 1)

    for label, status in (("설치 안내", {"state": "waiting_stable"}),
                          ("브랜드", {"state": "maintenance"})):
        check("{0} 화면은 예약하지 않는다 (이벤트만)".format(label),
              panel._next_wait(status) is None)

    left: Optional[float] = panel._next_wait({"state": "recording", "elapsed_s": 4})
    check("기록 화면은 측정 화면으로 넘어갈 때 깨어난다",
          left is not None and abs(left - (ep.MEASURE_AFTER_S - 4)) < 0.01, repr(left))

    # The one screen that still repeats: its number tracks the crane, and a
    # frozen reading is worse than none.
    period: Optional[float] = panel._next_wait({"state": "recording", "elapsed_s": 9999})
    check("측정 화면만 주기 갱신", period == 60.0, repr(period))
    slower = ep.PanelThread(_Recorder(), {"panel_refresh_seconds": 120})
    slower._started = time.monotonic() - (ep.BOOT_SPLASH_S + 1)
    check("주기는 설정에서 온다",
          slower._next_wait({"state": "recording", "elapsed_s": 9999}) == 120.0)

    panel._started = time.monotonic()
    check("스플래시는 다른 무엇보다 우선한다",
          panel._next_wait({"state": "recording", "elapsed_s": 9999}) is not None)


def test_frames() -> None:
    """Every screen has to render. A body that raises would blank the panel."""
    print("\n[3] 프레임 렌더링")
    base: dict[str, Any] = {
        "sensor_id": "pi-test", "position": "TOP", "state": "recording",
        "tilt_pct": 0.062, "temp_c": 26.3, "samples": 1200, "elapsed_s": 4.0,
        "ip": "192.168.0.19",
    }
    frames: dict[str, Any] = {}
    for label, extra in (("부팅", {"booting": True}),
                         ("설치", {"state": "waiting_stable"}),
                         ("기록", {}),
                         ("측정", {"elapsed_s": 9999}),
                         ("브랜드", {"state": "maintenance"})):
        status = dict(base, **extra)
        img = ep.render(status, rotate=0)
        frames[label] = img
        check("{0} 화면이 그려진다".format(label),
              img.size == (ep.WIDTH, ep.HEIGHT), str(img.size))

    # The boot frame exists to be unmistakable: it is the brand frame inverted,
    # which is what lets "the image changed" mean "the device came up".
    boot, brand = frames["부팅"], frames["브랜드"]
    check("부팅 화면은 브랜드 화면의 반전",
          list(boot.convert("L").tobytes()) ==
          [255 - v for v in brand.convert("L").tobytes()])

    check("주소가 없으면 NO NETWORK", ep.render(
        dict(base, ip=None), rotate=0) != frames["기록"])


def main() -> int:
    print("=" * 56)
    print("e-paper 패널 자가 점검")
    print("=" * 56)
    test_screens()
    test_wakeups()
    test_frames()
    print("\n{0} 실패".format(len(_failures)) if _failures else "\n전부 통과")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
