# coding:UTF-8
# Bring-up test for the GDEY0154D67 e-paper panel.
#
#   python eink_test.py            full sequence (white -> black -> pattern)
#   python eink_test.py --pattern  skip the flood fills, draw the pattern only
#
# The flood fills come first on purpose: if the SSD1681 init sequence or the bit
# polarity is wrong, an all-white/all-black screen shows it immediately, whereas a
# detailed pattern can look plausible while being mirrored or inverted.

from __future__ import annotations

import argparse
import os
import time
import sys

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

# dev/ tools live one level down, so put the repo root on the import path
# before reaching for app_config, ahrs_file and the rest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gdey0154d67 as epd

_FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(size: int):
    path = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    logger.warning("DejaVu font not found; falling back to the tiny bitmap font")
    return ImageFont.load_default()


def build_pattern() -> Image.Image:
    # Mode "1": 0 = black, 255 = white. Start white and draw in black.
    img = Image.new("1", (epd.WIDTH, epd.HEIGHT), 255)
    d = ImageDraw.Draw(img)

    # Border proves no rows/columns are lost at the edges.
    d.rectangle([0, 0, epd.WIDTH - 1, epd.HEIGHT - 1], outline=0, width=2)
    # A single diagonal reveals mirroring: it must run top-left to bottom-right.
    d.line([0, 0, epd.WIDTH - 1, epd.HEIGHT - 1], fill=0, width=1)
    # Corner block marks the origin, so rotation is unambiguous.
    d.rectangle([6, 6, 30, 30], fill=0)

    d.text((44, 10), "VERTICRANE", font=_font(18), fill=0)
    d.text((44, 34), "GDEY0154D67", font=_font(13), fill=0)
    d.text((14, 92), "Roll   0.000", font=_font(20), fill=0)
    d.text((14, 118), "Pitch  0.000", font=_font(20), fill=0)
    d.text((14, 150), "slope 0.000 %", font=_font(20), fill=0)
    d.text((14, 178), time.strftime("%Y-%m-%d %H:%M"), font=_font(12), fill=0)
    return img


def main() -> None:
    parser = argparse.ArgumentParser(description="GDEY0154D67 bring-up test.")
    parser.add_argument("--pattern", action="store_true",
                        help="Skip the white/black flood fills.")
    parser.add_argument("--loop", type=int, default=0,
                        help="Alternate white/black this many times and exit. Keeps the "
                             "charge pump running so the boost rails can be probed.")
    args = parser.parse_args()

    panel = epd.GDEY0154D67()
    panel.open()
    try:
        t0 = time.monotonic()
        panel.init()
        logger.info("init took {:.2f}s", time.monotonic() - t0)

        if args.loop:
            for i in range(args.loop):
                t = time.monotonic()
                panel.clear(white=(i % 2 == 0))
                logger.info("cycle {}/{} ({}) took {:.2f}s", i + 1, args.loop,
                            "white" if i % 2 == 0 else "black", time.monotonic() - t)
            panel.sleep()
            return

        if not args.pattern:
            for label, white in (("white", True), ("black", False)):
                t = time.monotonic()
                panel.clear(white=white)
                logger.info("{} fill took {:.2f}s", label, time.monotonic() - t)
                time.sleep(1.0)

        t = time.monotonic()
        panel.display(epd.image_to_frame(build_pattern()))
        logger.info("pattern refresh took {:.2f}s", time.monotonic() - t)
        panel.sleep()
    finally:
        panel.close()


if __name__ == "__main__":
    main()
