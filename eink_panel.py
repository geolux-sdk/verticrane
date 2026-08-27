# coding:UTF-8
# The e-paper panel: measurement status and permanent installation label (section 9).
#
#   python eink_panel.py --out panel.png      render to a file, no hardware needed
#   python eink_panel.py --demo               draw a sample frame on the panel
#
# The panel keeps its image without power, so this is not only a status display:
# it is the label on the device. A unit sitting switched off on site still says
# which sensor it is, where it belongs on the crane, and which way up it goes.
#
# It shows readings but never judges them. Whether a tilt is acceptable is for
# the server that collects the recordings to decide; this device records.
#
# That is also why it refreshes once a minute and no faster. A full refresh takes
# about 1.4 s and the panel is rated in refresh count -- at 1 Hz it would be worn
# out in roughly two weeks. Live numbers belong in the browser, not here.

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

import ahrs_file as af

WIDTH: int = 200
HEIGHT: int = 200

# Layout bands. Kept as constants because 200x200 leaves no room to guess.
TITLE_H: int = 26
FOOT_Y: int = 156

# Which screen to draw. The panel shows one thing at a time because 200x200
# cannot show three, and because what matters changes completely between
# mounting the device, leaving it to record, and coming back for the files.
SCREEN_INSTALL: str = "install"   # being fitted: which way up, which face down
SCREEN_MEASURE: str = "measure"   # left alone: is it recording, and what does it read
SCREEN_BRAND: str = "brand"       # operator is connected: nothing to decide here

# How long a recording runs before the panel stops showing the mounting guide.
# The operator may still be up the crane for the first minute.
MEASURE_AFTER_S: float = 60.0

# The Pi carries DejaVu; the Windows entries exist only so the layout can be
# previewed off the device. Falling through to the bitmap default silently
# shrinks every number, which made an early preview look like a layout bug.
_LATIN_REGULAR: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
_LATIN_BOLD: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
_KOREAN_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf",
    "C:/Windows/Fonts/malgun.ttf",
)

CONTACT_KO: str = "접촉면"
CONTACT_EN: str = "CONTACT"

_korean_font_path: Optional[str] = None
_warned_no_korean: bool = False


def korean_font_path() -> Optional[str]:
    global _korean_font_path
    if _korean_font_path is None:
        for path in _KOREAN_CANDIDATES:
            if os.path.exists(path):
                _korean_font_path = path
                break
        else:
            _korean_font_path = ""
    return _korean_font_path or None


def _font(size: int, bold: bool = False, korean: bool = False):
    if korean:
        path: Optional[str] = korean_font_path()
        if path:
            return ImageFont.truetype(path, size)
    for candidate in (_LATIN_BOLD if bold else _LATIN_REGULAR):
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    logger.warning("No scalable font found; the panel will be barely readable")
    return ImageFont.load_default()


def _label(text_ko: str, text_en: str) -> tuple[str, bool]:
    """Korean if a font can draw it, else the English stand-in.

    Never fails: a caption in the wrong language still says something, whereas
    a row of boxes says nothing. It just notes the missing font once in the log.
    """
    global _warned_no_korean
    if korean_font_path():
        return text_ko, True
    if not _warned_no_korean:
        logger.warning("No Korean font found; panel text falls back to English. "
                       "Install it with: sudo apt install fonts-nanum")
        _warned_no_korean = True
    return text_en, False


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, size: int,
         bold: bool = False, korean: bool = False, floor: int = 8):
    """Largest font at or below `size` whose text fits `width`.

    A warning clipped at both ends says nothing. The panel is 200 px wide and
    the strings are fixed, so shrinking to fit beats guessing a size.
    """
    while size > floor:
        font = _font(size, bold=bold, korean=korean)
        if draw.textlength(text, font=font) <= width:
            return font
        size -= 1
    return _font(floor, bold=bold, korean=korean)


def _right(draw: ImageDraw.ImageDraw, xr: int, y: int, text: str, font, fill: int) -> None:
    draw.text((xr - draw.textlength(text, font=font), y), text, font=font, fill=fill)


def _centre(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill: int,
            x0: int = 0, x1: int = WIDTH) -> None:
    draw.text(((x0 + x1 - draw.textlength(text, font=font)) / 2, y),
              text, font=font, fill=fill)


# --------------------------------------------------------------------------
# The axis / contact-face diagram
# --------------------------------------------------------------------------

def draw_orientation(d: ImageDraw.ImageDraw, x: int, y: int,
                     contact_face: str = "bottom", scale: float = 1.0) -> None:
    """One picture answering both "which way up" and "which face goes down".

    Drawn from the side. Z points up because that is what the sensor reports:
    at rest it reads +1 g on Z, and an accelerometer loads +1 g onto whichever
    axis points up. The hatched edge is the face that meets the structure, and
    Z runs away from it.
    """
    def u(v: float) -> int:
        return int(round(v * scale))

    box_l, box_r = x + u(14), x + u(66)
    box_t, box_b = y + u(30), y + u(60)

    # Z arrow, rising out of the top face.
    zx: int = (box_l + box_r) // 2
    d.line([zx, box_t - 2, zx, y + u(6)], fill=0, width=2)
    d.polygon([(zx, y), (zx - u(5), y + u(9)), (zx + u(5), y + u(9))], fill=0)
    d.text((zx + u(7), y + u(1)), "Z", font=_font(u(13), bold=True), fill=0)

    d.rectangle([box_l, box_t, box_r, box_b], outline=0, width=2)

    # Y comes out of the page towards the viewer; X runs to the right.
    cy: int = (box_t + box_b) // 2
    d.ellipse([box_l + u(8), cy - u(6), box_l + u(20), cy + u(6)], outline=0, width=1)
    d.ellipse([box_l + u(13), cy - 1, box_l + u(15), cy + 1], fill=0)
    d.text((box_l + u(22), cy - u(6)), "Y", font=_font(u(10)), fill=0)

    d.line([box_r - u(24), cy, box_r - u(8), cy], fill=0, width=2)
    d.polygon([(box_r - u(5), cy), (box_r - u(11), cy - u(4)),
               (box_r - u(11), cy + u(4))], fill=0)
    d.text((box_r - u(22), cy - u(16)), "X", font=_font(u(10)), fill=0)

    # The contact face: a hatched band on whichever edge meets the structure.
    t: int = u(11)
    edges: dict[str, tuple[int, int, int, int]] = {
        "bottom": (box_l, box_b + u(3), box_r, box_b + t),
        "top":    (box_l, box_t - t, box_r, box_t - u(3)),
        "left":   (box_l - t, box_t, box_l - u(3), box_b),
        "right":  (box_r + u(3), box_t, box_r + t, box_b),
    }
    ex0, ey0, ex1, ey1 = edges.get(contact_face, edges["bottom"])
    d.rectangle([ex0, ey0, ex1, ey1], outline=0, width=1)
    for hx in range(ex0, ex1, u(4) or 4):
        d.line([hx, ey1, min(hx + u(6), ex1), ey0], fill=0, width=1)

    text, ko = _label(CONTACT_KO, CONTACT_EN)
    width: int = u(80)
    _centre(d, ey1 + 3, text, _fit(d, text, width - 2, u(11), korean=ko, floor=7),
            0, x, x + width)


# --------------------------------------------------------------------------
# The frame
# --------------------------------------------------------------------------

STATE_SHORT: dict[str, str] = {
    "waiting_http": "WAIT", "waiting_stable": "SETTLE",
    "recording": "REC", "maintenance": "IDLE",
}


# The panel sits rotated in the enclosure, so the finished frame is turned
# before it goes out. Drawing stays in one upright coordinate system -- laying
# the text out sideways instead would make every offset in here need a mental
# rotation to check. Square panel, so nothing has to be re-fitted.
ROTATIONS: dict[int, int] = {
    90: Image.Transpose.ROTATE_270,     # PIL counts anticlockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def pick_screen(status: dict[str, Any]) -> str:
    """Which of the three screens this moment calls for."""
    if status.get("state") == "maintenance":
        return SCREEN_BRAND
    if (status.get("state") == "recording"
            and float(status.get("elapsed_s") or 0) >= MEASURE_AFTER_S):
        return SCREEN_MEASURE
    return SCREEN_INSTALL


def render(status: dict[str, Any], contact_face: str = "bottom",
           rotate: int = 90, ssid: Optional[str] = None) -> Image.Image:
    img = Image.new("1", (WIDTH, HEIGHT), 255)
    d = ImageDraw.Draw(img)

    _title(d, status)
    screen: str = pick_screen(status)
    if screen == SCREEN_BRAND:
        _body_brand(d)
    elif screen == SCREEN_MEASURE:
        _body_measure(d, status)
    else:
        _body_install(d, contact_face)
    _footer(d, status, ssid)

    transpose = ROTATIONS.get(rotate % 360)
    return img.transpose(transpose) if transpose else img


def _title(d: ImageDraw.ImageDraw, status: dict[str, Any]) -> None:
    """Who this device is. On every screen -- three of them share one crane."""
    position: str = status.get("position") or "UNSET"
    d.rectangle([0, 0, WIDTH - 1, TITLE_H - 1], fill=0)
    pos_font = _font(14, bold=True)
    pos_w: float = d.textlength(position, font=pos_font)
    sensor_id: str = str(status.get("sensor_id") or "verticrane")
    d.text((5, 5), sensor_id,
           font=_fit(d, sensor_id, WIDTH - 16 - int(pos_w), 14, bold=True), fill=255)
    _right(d, WIDTH - 5, 5, position, pos_font, 255)


def _body_install(d: ImageDraw.ImageDraw, contact_face: str) -> None:
    """Being fitted. Nothing to read yet -- only which way it goes on.

    This is what the panel shows from the moment the power comes on, because
    that is when the operator is carrying it up the crane to mount it.
    """
    draw_orientation(d, 22, TITLE_H + 4, contact_face, scale=1.45)


def _body_measure(d: ImageDraw.ImageDraw, status: dict[str, Any]) -> None:
    """Left alone and recording. The mounting guide has done its job."""
    tilt: Optional[float] = status.get("tilt_pct")
    _centre(d, TITLE_H + 8, "-" if tilt is None else "{0:.3f}".format(tilt),
            _font(46, bold=True), 0)
    _centre(d, TITLE_H + 58, "% tilt", _font(14), 0)
    _centre(d, TITLE_H + 78, _temp(status.get("temp_c")), _font(30, bold=True), 0)

    line: str = "{0}  {1}   {2} smp".format(
        STATE_SHORT.get(str(status.get("state")), "?"),
        _hms(status.get("elapsed_s", 0)), status.get("samples", 0))
    _centre(d, FOOT_Y - 20, line, _fit(d, line, WIDTH - 10, 14, bold=True), 0)


def _body_brand(d: ImageDraw.ImageDraw) -> None:
    """An operator is connected, so the panel has nothing left to tell them --
    the browser says it better. It carries the name instead."""
    _centre(d, TITLE_H + 34, "GEOLUX", _fit(d, "GEOLUX", WIDTH - 16, 44, bold=True), 0)
    d.line([28, TITLE_H + 88, WIDTH - 29, TITLE_H + 88], fill=0, width=2)
    _centre(d, TITLE_H + 96, "verticrane", _font(15), 0)


def _footer(d: ImageDraw.ImageDraw, status: dict[str, Any],
            ssid: Optional[str]) -> None:
    """How to reach it. No clock: an unsynced one would be worse than none.

    The SSID is inverted while the device is actually on that network. Joined or
    not is the first thing an operator wants to know when the page will not
    load, and a solid bar answers it across a site without reading anything.
    """
    d.line([4, FOOT_Y, WIDTH - 5, FOOT_Y], fill=0, width=1)
    ip: Optional[str] = status.get("ip")
    d.text((5, FOOT_Y + 4), ip or "NO NETWORK", font=_font(15, bold=bool(ip)), fill=0)
    if not ssid:
        return

    label: str = "wifi " + ssid
    font = _fit(d, label, WIDTH - 16, 14)
    y: int = FOOT_Y + 23
    if ip:
        d.rectangle([4, y - 2, WIDTH - 5, y + 17], fill=0)
        d.text((8, y), label, font=font, fill=255)
    else:
        d.text((8, y), label, font=font, fill=0)


def _temp(value: Optional[float]) -> str:
    return "{0:.1f}°C".format(value) if value is not None else "--°C"


def _hms(seconds: float) -> str:
    s = int(seconds)
    return "{0:d}:{1:02d}:{2:02d}".format(s // 3600, (s // 60) % 60, s % 60)


def wifi_ssid(configured: str = "") -> Optional[str]:
    """The network to join, not the one currently joined.

    Up the crane there is no connection, and that is exactly when the operator
    needs to be told which SSID to look for. So this reports the *saved* profile
    rather than the active one -- the most recently used, since that is what
    NetworkManager will reach for first, with "+N" when others are saved too.
    """
    if configured:
        return configured
    try:
        # TIMESTAMP is when the profile was last used. Once a second network is
        # saved, taking the first row would show whichever nmcli happened to list
        # first -- which is not the one the operator should be looking for.
        out: str = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,TIMESTAMP", "connection", "show"],
            capture_output=True, text=True, timeout=5).stdout
        saved: list[tuple[int, str]] = []
        for line in out.splitlines():
            parts = line.rsplit(":", 2)
            if len(parts) != 3:
                continue
            name, kind, stamp = parts
            if "wireless" not in kind or not name:
                continue
            try:
                used = int(stamp)
            except ValueError:
                used = 0
            saved.append((used, name))
        if saved:
            saved.sort(reverse=True)
            # "+N" when other networks are saved too. The most recently used one
            # is the best single guess at what the device will rejoin, but it is
            # a guess -- and a panel that named it alone would read as a fact.
            extra: str = "  +{0}".format(len(saved) - 1) if len(saved) > 1 else ""
            return saved[0][1] + extra
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        # The profile contents need root, but the filename is the SSID and the
        # directory itself is readable.
        for name in sorted(os.listdir("/etc/NetworkManager/system-connections")):
            return name.rsplit(".nmconnection", 1)[0]
    except OSError:
        pass
    return None


def local_ip() -> Optional[str]:
    """The address the operator would type. None when the WiFi has dropped."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))       # no packet is sent; just picks a route
            return s.getsockname()[0]
    except OSError:
        return None


# --------------------------------------------------------------------------
# Driving the hardware
# --------------------------------------------------------------------------

def show(img: Image.Image) -> bool:
    """Push a frame to the panel. False if the panel is absent or unhappy.

    Never raises: a recorder that dies because a display is missing has lost
    the measurement, which is the one thing that matters (section 9).
    """
    try:
        import gdey0154d67 as epd
    except ImportError as exc:
        logger.warning("e-paper driver unavailable: {}", exc)
        return False
    panel = epd.GDEY0154D67()
    try:
        panel.open()
        panel.init()
        panel.display(epd.image_to_frame(img))
        panel.sleep()
        return True
    except Exception as exc:                              # noqa: BLE001
        logger.error("Panel refresh failed: {}", exc)
        return False
    finally:
        try:
            panel.close()
        except Exception:                                 # noqa: BLE001
            pass


class PanelThread:
    """Refreshes the panel on its own thread (section 9).

    A refresh blocks on SPI for about 1.4 s. On the polling loop that would
    drop 35 samples every minute, so it runs here and only ever reads a
    snapshot the recorder has already prepared.
    """

    def __init__(self, recorder: Any, cfg: dict[str, Any]) -> None:
        self.recorder = recorder
        self.cfg = cfg
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_key: Optional[tuple] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="panel", daemon=True)
        self._thread.start()

    def refresh_now(self) -> None:
        """Called on a state change: those must not wait out the interval."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._draw()
            except Exception as exc:                      # noqa: BLE001
                # A missing or broken panel must never stop the recording.
                logger.error("Panel update failed: {}", exc)
            interval: float = float(self.cfg.get("panel_refresh_seconds", 60))
            self._wake.wait(max(interval, 10.0))
            self._wake.clear()

    def _draw(self) -> None:
        snap = self.recorder.snapshot()
        status: dict[str, Any] = {
            "sensor_id": snap.sensor_id,
            "position": snap.position,
            "state": snap.state,
            "tilt_pct": snap.tilt,
            "temp_c": snap.temp_c,
            "samples": snap.samples,
            "elapsed_s": snap.elapsed_s,
            "time_quality": snap.time_quality,
            "ip": local_ip(),
        }
        # Skip the refresh when nothing a reader would notice has changed: the
        # panel wears out by refresh count, so an idle device should not spend
        # them redrawing the same frame.
        key = (pick_screen(status), status["state"], status["position"], status["ip"],
               round(status["tilt_pct"], 3) if status["tilt_pct"] is not None else None,
               status["samples"] // 250)
        if key == self._last_key:
            return
        self._last_key = key
        show(render(status,
                    contact_face=str(self.cfg.get("contact_face", "bottom")),
                    rotate=int(self.cfg.get("panel_rotation", 90)),
                    ssid=wifi_ssid(str(self.cfg.get("wifi_ssid", "")))))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the e-paper status panel.")
    parser.add_argument("--out", help="Write a PNG instead of driving the panel.")
    parser.add_argument("--position", default="TOP", choices=["UNSET", "BASE", "MIDDLE", "TOP"])
    parser.add_argument("--screen", choices=[SCREEN_INSTALL, SCREEN_MEASURE, SCREEN_BRAND],
                        help="Force a screen instead of picking one from the state.")
    parser.add_argument("--contact-face", default="bottom",
                        choices=["bottom", "top", "left", "right"])
    parser.add_argument("--rotate", type=int, default=90, choices=[0, 90, 180, 270],
                        help="Clockwise rotation applied to the finished frame.")
    parser.add_argument("--scale", type=int, default=1, help="Enlarge the PNG for review.")
    args = parser.parse_args()

    demo: dict[str, Any] = {
        "sensor_id": socket.gethostname()[:16],
        "position": args.position,
        "state": "recording",
        "tilt_pct": 0.062,
        "temp_c": 26.3,
        "samples": 12475, "elapsed_s": 8123,
        "time_quality": af.QUALITY_SYNCED,
        "ip": local_ip(),
    }
    if args.screen == SCREEN_BRAND:
        demo["state"] = "maintenance"
    elif args.screen == SCREEN_INSTALL:
        demo["state"] = "waiting_stable"
    img = render(demo, contact_face=args.contact_face, rotate=args.rotate,
                 ssid=wifi_ssid())

    if args.out:
        if args.scale > 1:
            img = img.resize((WIDTH * args.scale, HEIGHT * args.scale), Image.NEAREST)
        img.convert("L").save(args.out)
        logger.info("Wrote {}", args.out)
        return 0

    return 0 if show(img) else 1


if __name__ == "__main__":
    sys.exit(main())
