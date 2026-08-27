# coding:UTF-8
# The e-paper panel: measurement status and permanent installation label (section 9).
#
#   python eink_panel.py --out panel.png      render to a file, no hardware needed
#   python eink_panel.py --demo               draw a sample frame on the panel
#
# The panel keeps its image without power, so this is not only a status display:
# it is the label on the device. A unit sitting switched off on site still says
# which sensor it is, which way up it goes, and not to turn it after mounting.
#
# That is also why it refreshes once a minute and no faster. A full refresh takes
# about 1.4 s and the panel is rated in refresh count -- at 1 Hz it would be worn
# out in roughly two weeks. Live numbers belong in the browser, not here.

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from typing import Any, Optional

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

import ahrs_file as af

WIDTH: int = 200
HEIGHT: int = 200

# Layout bands. Kept as constants because 200x200 leaves no room to guess.
TITLE_H: int = 26
WARN_Y: int = 171
FOOT_Y: int = 150

_DEJAVU: str = "/usr/share/fonts/truetype/dejavu"
_KOREAN_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf",
)

WARNING_KO: str = "설치 후 방향 변경 금지"
WARNING_EN: str = "DO NOT REORIENT AFTER FITTING"
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
    name: str = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = os.path.join(_DEJAVU, name)
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _label(text_ko: str, text_en: str) -> tuple[str, bool]:
    """Korean if a font can draw it, else the English stand-in.

    Losing the warning entirely would be worse than showing it in English, so
    this never fails -- it just says so once in the log.
    """
    global _warned_no_korean
    if korean_font_path():
        return text_ko, True
    if not _warned_no_korean:
        logger.warning("No Korean font found; panel text falls back to English. "
                       "Install it with: sudo apt install fonts-nanum")
        _warned_no_korean = True
    return text_en, False


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
                     contact_face: str = "bottom") -> None:
    """One picture answering both "which way up" and "which face goes down".

    Drawn from the side. Z points up because that is what the sensor reports:
    at rest it reads +1 g on Z, and an accelerometer loads +1 g onto whichever
    axis points up. The hatched edge is the face that meets the structure, and
    Z runs away from it.
    """
    box_l, box_r = x + 14, x + 66
    box_t, box_b = y + 30, y + 60

    # Z arrow, rising out of the top face.
    zx: int = (box_l + box_r) // 2
    d.line([zx, box_t - 2, zx, y + 6], fill=0, width=2)
    d.polygon([(zx, y), (zx - 5, y + 9), (zx + 5, y + 9)], fill=0)
    d.text((zx + 7, y + 1), "Z", font=_font(13, bold=True), fill=0)

    d.rectangle([box_l, box_t, box_r, box_b], outline=0, width=2)

    # Y comes out of the page towards the viewer; X runs to the right.
    cy: int = (box_t + box_b) // 2
    d.ellipse([box_l + 7, cy - 6, box_l + 19, cy + 6], outline=0, width=1)
    d.ellipse([box_l + 12, cy - 1, box_l + 14, cy + 1], fill=0)
    d.text((box_l + 6, cy + 7), "Y", font=_font(10), fill=0)

    d.line([box_r - 22, cy, box_r - 6, cy], fill=0, width=2)
    d.polygon([(box_r - 3, cy), (box_r - 9, cy - 4), (box_r - 9, cy + 4)], fill=0)
    d.text((box_r - 20, cy - 15), "X", font=_font(10), fill=0)

    # The contact face: a hatched band on whichever edge meets the structure.
    edges: dict[str, tuple[int, int, int, int]] = {
        "bottom": (box_l, box_b + 3, box_r, box_b + 11),
        "top":    (box_l, box_t - 11, box_r, box_t - 3),
        "left":   (box_l - 11, box_t, box_l - 3, box_b),
        "right":  (box_r + 3, box_t, box_r + 11, box_b),
    }
    ex0, ey0, ex1, ey1 = edges.get(contact_face, edges["bottom"])
    d.rectangle([ex0, ey0, ex1, ey1], outline=0, width=1)
    for hx in range(ex0, ex1, 4):
        d.line([hx, ey1, min(hx + 6, ex1), ey0], fill=0, width=1)

    text, ko = _label(CONTACT_KO, CONTACT_EN)
    _centre(d, ey1 + 3, text, _font(11 if ko else 9, korean=ko), 0, x, x + 80)


# --------------------------------------------------------------------------
# The frame
# --------------------------------------------------------------------------

STATE_SHORT: dict[str, str] = {
    "waiting_http": "WAIT", "waiting_stable": "SETTLE",
    "recording": "REC", "maintenance": "IDLE",
}


def render(status: dict[str, Any], threshold_pct: float = 0.1,
           contact_face: str = "bottom") -> Image.Image:
    img = Image.new("1", (WIDTH, HEIGHT), 255)
    d = ImageDraw.Draw(img)

    tilt: Optional[float] = status.get("tilt_pct")
    position: str = status.get("position") or "UNSET"
    alarm: bool = tilt is not None and tilt > threshold_pct

    # --- title bar: who this device is -----------------------------------
    d.rectangle([0, 0, WIDTH - 1, TITLE_H - 1], fill=0)
    d.text((5, 5), str(status.get("sensor_id") or "verticrane"),
           font=_font(14, bold=True), fill=255)
    _right(d, WIDTH - 5, 5, position, _font(14, bold=True), 255)

    # --- orientation diagram + the reading --------------------------------
    draw_orientation(d, 2, TITLE_H + 2, contact_face)

    if tilt is None:
        d.text((92, TITLE_H + 20), "--", font=_font(34, bold=True), fill=0)
    else:
        d.text((92, TITLE_H + 14), "{0:.3f}".format(tilt), font=_font(30, bold=True), fill=0)
        d.text((92, TITLE_H + 48), "%  tilt", font=_font(12), fill=0)
    if alarm:
        d.rectangle([90, TITLE_H + 66, WIDTH - 4, TITLE_H + 86], fill=0)
        _centre(d, TITLE_H + 69, "ALARM", _font(14, bold=True), 255, 90, WIDTH - 4)
    elif status.get("state") == "recording":
        _centre(d, TITLE_H + 69, "REC {0}".format(_hms(status.get("elapsed_s", 0))),
                _font(13, bold=True), 0, 90, WIDTH - 4)

    # --- angles and running totals ---------------------------------------
    d.line([4, FOOT_Y - 32, WIDTH - 5, FOOT_Y - 32], fill=0, width=1)
    small = _font(11)
    roll, pitch = status.get("roll"), status.get("pitch")
    d.text((5, FOOT_Y - 29), "R {0}  P {1}".format(_deg(roll), _deg(pitch)), font=small, fill=0)
    d.text((5, FOOT_Y - 17), "{0}  {1} smp  {2}".format(
        STATE_SHORT.get(str(status.get("state")), "?"),
        status.get("samples", 0), _temp(status.get("temp_c"))), font=small, fill=0)

    # --- how to reach it, and when this was drawn -------------------------
    d.line([4, FOOT_Y, WIDTH - 5, FOOT_Y], fill=0, width=1)
    tiny = _font(10)
    d.text((5, FOOT_Y + 4), status.get("ip") or "NO NETWORK", font=tiny, fill=0)
    stamp: str = time.strftime("%H:%M:%S")
    if status.get("time_quality") not in af.TRUSTED_QUALITIES:
        stamp += " ?"      # the clock is not trusted; the file name says so too
    _right(d, WIDTH - 5, FOOT_Y + 4, stamp, tiny, 0)

    # --- the warning that has to outlive the power ------------------------
    d.rectangle([0, WARN_Y, WIDTH - 1, HEIGHT - 1], fill=0)
    text, ko = _label(WARNING_KO, WARNING_EN)
    font = _font(15 if ko else 11, bold=True, korean=ko)
    _centre(d, WARN_Y + (9 if ko else 11), text, font, 255)

    if position == "UNSET":
        # Same signal the filename carries, made visible on site.
        d.rectangle([WIDTH - 62, TITLE_H + 90, WIDTH - 4, TITLE_H + 108], fill=0)
        _centre(d, TITLE_H + 92, "UNSET", _font(12, bold=True), 255, WIDTH - 62, WIDTH - 4)
    return img


def _deg(value: Optional[float]) -> str:
    return "{0:+.2f}".format(value) if value is not None else "  --  "


def _temp(value: Optional[float]) -> str:
    return "{0:.1f}C".format(value) if value is not None else "--C"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the e-paper status panel.")
    parser.add_argument("--out", help="Write a PNG instead of driving the panel.")
    parser.add_argument("--position", default="TOP", choices=["UNSET", "BASE", "MIDDLE", "TOP"])
    parser.add_argument("--contact-face", default="bottom",
                        choices=["bottom", "top", "left", "right"])
    parser.add_argument("--alarm", action="store_true", help="Render the alarm state.")
    parser.add_argument("--scale", type=int, default=1, help="Enlarge the PNG for review.")
    args = parser.parse_args()

    demo: dict[str, Any] = {
        "sensor_id": socket.gethostname()[:16],
        "position": args.position,
        "state": "recording",
        "tilt_pct": 0.842 if args.alarm else 0.123,
        "roll": -1.086, "pitch": -0.061, "temp_c": 26.3,
        "samples": 12475, "elapsed_s": 8123,
        "time_quality": af.QUALITY_SYNCED,
        "ip": local_ip(),
    }
    img = render(demo, contact_face=args.contact_face)

    if args.out:
        if args.scale > 1:
            img = img.resize((WIDTH * args.scale, HEIGHT * args.scale), Image.NEAREST)
        img.convert("L").save(args.out)
        logger.info("Wrote {}", args.out)
        return 0

    return 0 if show(img) else 1


if __name__ == "__main__":
    sys.exit(main())
