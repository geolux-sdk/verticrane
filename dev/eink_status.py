# coding:UTF-8
# Measure the tilt for a fixed window and show the result on the e-paper panel.
#
#   python eink_status.py                 measure 60 s, then draw
#   python eink_status.py --seconds 30    shorter window
#   python eink_status.py --no-display    print only, leave the panel alone
#
# The panel keeps its image without power, so a single refresh at the end of the
# window is all that is needed -- refreshing while sampling would only add wear and
# block the serial loop for 1.4 s at a time.

from __future__ import annotations

import argparse
import math
import os
import statistics
import time
from typing import Optional

from loguru import logger
from PIL import Image, ImageDraw, ImageFont
import sys

# dev/ tools live one level down, so put the repo root on the import path
# before reaching for app_config, ahrs_file and the rest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_config
import gdey0154d67 as epd
import port_config
import read_status

from log_tilt import resultant_slope_pct

SAMPLE_RATE_HZ = 25.0
SAMPLE_PERIOD_S = 1.0 / SAMPLE_RATE_HZ
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"

_cfg = app_config.load()
SLOPE_THRESHOLD_PCT: float = float(_cfg["slope_threshold_pct"])


def _font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = os.path.join(_FONT_DIR, name)
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    logger.warning("DejaVu font not found; falling back to the bitmap font")
    return ImageFont.load_default()


def measure(port: str, seconds: float) -> Optional[dict]:
    device, baud = read_status.connectAutoBaud(port)
    if device is None:
        logger.error("No response from the sensor on {}", port)
        return None
    try:
        device.verbose = False
        rolls: list[float] = []
        pitches: list[float] = []
        slopes: list[float] = []
        attempts = 0

        start = time.perf_counter()
        next_tick = start
        while time.perf_counter() - start < seconds:
            device.readReg(0x34, 15)
            attempts += 1
            roll = device.deviceData.get("AngX")
            pitch = device.deviceData.get("AngY")
            if roll is not None and pitch is not None:
                rolls.append(roll)
                pitches.append(pitch)
                slopes.append(resultant_slope_pct(roll, pitch))
            next_tick += SAMPLE_PERIOD_S
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
        elapsed = time.perf_counter() - start

        device.readReg(0x43, 1)
        temp = device.deviceData.get("Temp")
    finally:
        device.closeDevice()

    if not slopes:
        logger.error("No usable samples in {:.0f}s", seconds)
        return None

    return {
        "baud": baud,
        "elapsed": elapsed,
        "samples": len(slopes),
        "attempts": attempts,
        "rate": len(slopes) / elapsed if elapsed > 0 else 0.0,
        "roll": statistics.fmean(rolls),
        "pitch": statistics.fmean(pitches),
        "slope": statistics.fmean(slopes),
        "slope_max": max(slopes),
        "slope_pp": max(slopes) - min(slopes),
        # Population stdev: this is the whole measurement window, not a sample of one.
        "slope_sd": statistics.pstdev(slopes) if len(slopes) > 1 else 0.0,
        "temp": temp,
    }


def render(m: dict) -> Image.Image:
    img = Image.new("1", (epd.WIDTH, epd.HEIGHT), 255)
    d = ImageDraw.Draw(img)
    alarm = m["slope_max"] > SLOPE_THRESHOLD_PCT

    # Inverted title bar: readable at a glance across a site, and it doubles as the
    # alarm indicator so the state is obvious without reading the numbers.
    d.rectangle([0, 0, epd.WIDTH - 1, 25], fill=0)
    d.text((6, 4), "VERTICRANE", font=_font(15, bold=True), fill=255)
    state = "ALARM" if alarm else "OK"
    w = d.textlength(state, font=_font(15, bold=True))
    d.text((epd.WIDTH - 8 - w, 4), state, font=_font(15, bold=True), fill=255)

    d.text((6, 32), "slope", font=_font(12), fill=0)
    d.text((6, 44), "{0:.3f}".format(m["slope"]), font=_font(40, bold=True), fill=0)
    d.text((150, 66), "%", font=_font(16), fill=0)

    d.text((6, 92), "max {0:.3f}   p-p {1:.3f}".format(m["slope_max"], m["slope_pp"]),
           font=_font(12), fill=0)
    d.text((6, 106), "sd  {0:.4f}   thr {1:g}".format(m["slope_sd"], SLOPE_THRESHOLD_PCT),
           font=_font(12), fill=0)

    d.line([6, 124, epd.WIDTH - 7, 124], fill=0, width=1)

    d.text((6, 130), "Roll", font=_font(13), fill=0)
    d.text((62, 130), "{0:+8.3f}".format(m["roll"]), font=_font(15, bold=True), fill=0)
    d.text((160, 132), "deg", font=_font(11), fill=0)
    d.text((6, 150), "Pitch", font=_font(13), fill=0)
    d.text((62, 150), "{0:+8.3f}".format(m["pitch"]), font=_font(15, bold=True), fill=0)
    d.text((160, 152), "deg", font=_font(11), fill=0)

    d.line([6, 172, epd.WIDTH - 7, 172], fill=0, width=1)

    temp = "{0:.1f}C".format(m["temp"]) if m["temp"] is not None else "-"
    d.text((6, 176), "{0}s  {1} smp  {2:.1f}Hz  {3}".format(
        round(m["elapsed"]), m["samples"], m["rate"], temp), font=_font(10), fill=0)
    d.text((6, 188), time.strftime("%Y-%m-%d %H:%M:%S"), font=_font(10), fill=0)
    return img


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure the tilt, then show the result on the e-paper panel.")
    port_config.add_port_argument(parser)
    parser.add_argument("--seconds", type=float, default=60.0,
                        help="Measurement window in seconds (default 60).")
    parser.add_argument("--no-display", action="store_true",
                        help="Print the result without touching the panel.")
    args = parser.parse_args()

    port = port_config.resolve_port(args.port)
    logger.info("Measuring {:.0f}s on {}", args.seconds, port)
    m = measure(port, args.seconds)
    if m is None:
        raise SystemExit(1)

    logger.info("slope {:.3f}% (max {:.3f}, p-p {:.3f}, sd {:.4f})",
                m["slope"], m["slope_max"], m["slope_pp"], m["slope_sd"])
    logger.info("Roll {:+.3f} deg  Pitch {:+.3f} deg  {}/{} samples at {:.1f} Hz",
                m["roll"], m["pitch"], m["samples"], m["attempts"], m["rate"])
    if args.no_display:
        return

    panel = epd.GDEY0154D67()
    panel.open()
    try:
        panel.init()
        panel.display(epd.image_to_frame(render(m)))
        panel.sleep()
        logger.info("Panel updated")
    finally:
        panel.close()


if __name__ == "__main__":
    main()
