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
# A full refresh takes about 1.4 s and the panel is rated in refresh count, so
# it is driven by events, not by a clock: something happens, the frame is drawn
# once, and the thread goes back to sleep indefinitely. A device left alone in a
# state nobody is watching spends no refreshes at all.
#
# The measurement screen is the one exception. It carries a live number, and a
# number that silently stops tracking the crane is worse than no number, so that
# screen -- and only that screen -- also refreshes on a timer.

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from typing import Any, Optional

from loguru import logger
from PIL import Image, ImageChops, ImageDraw, ImageFont

import ahrs_file as af

WIDTH: int = 200
HEIGHT: int = 200

# Layout bands. Kept as constants because 200x200 leaves no room to guess.
TITLE_H: int = 26
FOOT_Y: int = 166

# Which screen to draw. The panel shows one thing at a time because 200x200
# cannot show three, and because what matters changes completely between
# mounting the device, leaving it to record, and coming back for the files.
SCREEN_BOOT: str = "boot"         # just came up: the brand frame, inverted
SCREEN_INSTALL: str = "install"   # being fitted: which way up, which face down
SCREEN_RECORDING: str = "record"  # recording has begun; no reading to show yet
SCREEN_MEASURE: str = "measure"   # left alone: what it reads
SCREEN_BRAND: str = "brand"       # operator is connected: nothing to decide here

# The boot frame is the brand frame inverted, and that is the whole point. The
# panel keeps its image without power, so whatever was on it before the power
# cut is still there when the power returns -- an unchanged frame cannot say
# "this device is running". A photo negative of the previous frame can.
#
# It says the recorder started, which is not the same as the power arriving:
# kernel and userspace take about 24 s on a Pi Zero 2 W, and the first SPI
# refresh a further 2 s. Nothing drawn from Python can close that gap.
BOOT_SPLASH_S: float = 10.0

# How long the RECORDING frame stands before the reading replaces it. Long
# enough for someone beside the device to see that it started, short enough
# that they are not left waiting for a number.
#
# It used to be 60 s, to hold the mounting guide up while the operator was
# still on the crane. The mount delay already covers the climb, and the
# RECORDING frame now says what the guide could not.
MEASURE_AFTER_S: float = 15.0

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
    """Which screen this moment calls for.

    The order matters: booting outranks everything because for those first
    seconds the only question anyone has is whether the device came up.
    """
    if status.get("booting"):
        return SCREEN_BOOT
    state: Any = status.get("state")
    if state == "maintenance":
        return SCREEN_BRAND
    if state == "recording":
        if float(status.get("elapsed_s") or 0) >= MEASURE_AFTER_S:
            return SCREEN_MEASURE
        return SCREEN_RECORDING
    return SCREEN_INSTALL


def render(status: dict[str, Any], contact_face: str = "bottom",
           rotate: int = 90) -> Image.Image:
    img = Image.new("1", (WIDTH, HEIGHT), 255)
    d = ImageDraw.Draw(img)

    _title(d, status)
    screen: str = pick_screen(status)
    if screen in (SCREEN_BRAND, SCREEN_BOOT):
        _body_brand(d)
    elif screen == SCREEN_RECORDING:
        _body_recording(d, status)
    elif screen == SCREEN_MEASURE:
        _body_measure(d, status)
    else:
        _body_install(d, contact_face)
    _footer(d, status)

    if screen == SCREEN_BOOT:
        # Inverted whole, title bar included, so it cannot be mistaken for any
        # frame the device draws while it is running.
        img = ImageChops.invert(img)

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


def _body_recording(d: ImageDraw.ImageDraw, status: dict[str, Any]) -> None:
    """Recording has started, and that is the entire message.

    Reading this from the ground is the point, so it is one word at the size
    the panel can manage. The elapsed time underneath is what separates a live
    frame from one left behind by a power cut.
    """
    label, korean = _label("기록 중", "RECORDING")
    d.rectangle([0, TITLE_H + 6, WIDTH - 1, TITLE_H + 58], fill=0)
    _centre(d, TITLE_H + 11, label,
            _fit(d, label, WIDTH - 16, 38, bold=True, korean=korean), 255)
    _centre(d, TITLE_H + 74, _hms(status.get("elapsed_s", 0)), _font(34, bold=True), 0)


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


def _footer(d: ImageDraw.ImageDraw, status: dict[str, Any]) -> None:
    """The address, which is the only thing here anyone types.

    No clock: an unsynced one is worse than none, and the trustworthy time is in
    the filename. No SSID either -- connected, the operator is already on that
    network and knows it; disconnected, there is no network it "should" join,
    since NetworkManager takes whichever is in range. This line answers the
    question that has an answer: reachable, and at what address.
    """
    d.line([4, FOOT_Y, WIDTH - 5, FOOT_Y], fill=0, width=1)
    ip: Optional[str] = status.get("ip")
    if ip:
        _centre(d, FOOT_Y + 8, ip, _fit(d, ip, WIDTH - 12, 16, bold=True), 0)
    else:
        d.rectangle([4, FOOT_Y + 4, WIDTH - 5, HEIGHT - 3], fill=0)
        _centre(d, FOOT_Y + 8, "NO NETWORK",
                _fit(d, "NO NETWORK", WIDTH - 24, 16, bold=True), 255)


def _temp(value: Optional[float]) -> str:
    return "{0:.1f}°C".format(value) if value is not None else "--°C"


def _hms(seconds: float) -> str:
    s = int(seconds)
    return "{0:d}:{1:02d}:{2:02d}".format(s // 3600, (s // 60) % 60, s % 60)


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
    """Draws the panel when something happens, not on a clock (section 9).

    A refresh blocks on SPI for about 1.4 s. On the polling loop that would
    drop 35 samples every minute, so it runs here and only ever reads a
    snapshot the recorder has already prepared.

    The thread sleeps on an event with no timeout. Whoever changes something
    the panel shows calls refresh_now(); a device sitting untouched in a state
    nobody is watching spends no refreshes at all. Two things are genuinely
    time-based rather than event-based, and both are one-shot deadlines rather
    than a repeating tick: the end of the boot frame, and the moment the
    RECORDING frame gives way to the reading.

    The measurement screen is the exception that does repeat. Its number tracks
    the crane, and a number frozen an hour ago is worse than none.
    """

    def __init__(self, recorder: Any, cfg: dict[str, Any]) -> None:
        self.recorder = recorder
        self.cfg = cfg
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_frame: Optional[bytes] = None
        self._thread: Optional[threading.Thread] = None
        self._started: float = time.monotonic()

    def start(self) -> None:
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="panel", daemon=True)
        self._thread.start()

    def refresh_now(self) -> None:
        """Something the panel shows has changed. Draw it now.

        Cheap to call and safe to over-call: the frame is only pushed to the
        panel when the drawn content actually differs.
        """
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            status: Optional[dict[str, Any]] = None
            try:
                status = self._draw()
            except Exception as exc:                      # noqa: BLE001
                # A missing or broken panel must never stop the recording.
                logger.error("Panel update failed: {}", exc)
            if self._stop.is_set():
                break
            self._wake.wait(self._next_wait(status))
            self._wake.clear()

    def _next_wait(self, status: Optional[dict[str, Any]]) -> Optional[float]:
        """Seconds until the next frame is due, or None to wait for an event.

        None is the normal answer. Event.wait(None) blocks until refresh_now()
        is called, which is what makes an idle device cost nothing.
        """
        splash_left: float = (self._started + BOOT_SPLASH_S) - time.monotonic()
        if splash_left > 0:
            return splash_left
        if status is None:
            return None
        screen: str = pick_screen(status)
        if screen == SCREEN_RECORDING:
            # The one-shot handover to the reading.
            left: float = MEASURE_AFTER_S - float(status.get("elapsed_s") or 0)
            if left > 0:
                return left
        if screen == SCREEN_MEASURE:
            return max(float(self.cfg.get("panel_refresh_seconds", 60)), 10.0)
        return None

    def _status(self) -> dict[str, Any]:
        snap = self.recorder.snapshot()
        return {
            "sensor_id": snap.sensor_id,
            "position": snap.position,
            "state": snap.state,
            "tilt_pct": snap.tilt,
            "temp_c": snap.temp_c,
            "samples": snap.samples,
            "elapsed_s": snap.elapsed_s,
            "time_quality": snap.time_quality,
            "ip": local_ip(),
            "booting": (time.monotonic() - self._started) < BOOT_SPLASH_S,
        }

    def _draw(self) -> dict[str, Any]:
        """Render every time, push only when the picture actually differs.

        Comparing the finished frame rather than the fields behind it is what
        makes refresh_now() safe to over-call. Comparing fields meant guessing
        which ones each screen reads, and guessing wrong cost a refresh: the
        boot frame ignores the state, so the recorder settling its first state
        underneath it used to redraw the identical picture.

        Rendering is a few milliseconds of PIL against 1.4 s of SPI, so the
        render that turns out to be redundant is the cheap half.
        """
        status: dict[str, Any] = self._status()
        img = render(status,
                     contact_face=str(self.cfg.get("contact_face", "bottom")),
                     rotate=int(self.cfg.get("panel_rotation", 90)))
        frame: bytes = img.tobytes()
        if frame == self._last_frame:
            return status
        self._last_frame = frame
        show(img)
        return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the e-paper status panel.")
    parser.add_argument("--out", help="Write a PNG instead of driving the panel.")
    parser.add_argument("--position", default="TOP", choices=["UNSET", "BASE", "MIDDLE", "TOP"])
    parser.add_argument("--screen", choices=[SCREEN_BOOT, SCREEN_INSTALL, SCREEN_RECORDING,
                                             SCREEN_MEASURE, SCREEN_BRAND],
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
    if args.screen == SCREEN_BOOT:
        demo["booting"] = True
    elif args.screen == SCREEN_BRAND:
        demo["state"] = "maintenance"
    elif args.screen == SCREEN_INSTALL:
        demo["state"] = "waiting_stable"
    elif args.screen == SCREEN_RECORDING:
        demo["elapsed_s"] = 4
    img = render(demo, contact_face=args.contact_face, rotate=args.rotate)

    if args.out:
        if args.scale > 1:
            img = img.resize((WIDTH * args.scale, HEIGHT * args.scale), Image.NEAREST)
        img.convert("L").save(args.out)
        logger.info("Wrote {}", args.out)
        return 0

    return 0 if show(img) else 1


if __name__ == "__main__":
    sys.exit(main())
