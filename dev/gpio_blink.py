# coding:UTF-8
# Toggle a GPIO pin so it can be checked with an LED or a multimeter.
#
#   python gpio_blink.py --pin 25              blink GPIO25 (the panel's DC line)
#   python gpio_blink.py --pin 4 --hz 2        blink a spare pin at 2 Hz
#
# Useful for proving the wire from the Pi's header to the adapter board actually
# carries the signal: a panel that answers on SPI already implies the bus works, so
# this is aimed at the individual control lines and at hardware sanity checks.
#
# An LED needs a series resistor (330 ohm is fine) with its cathode to GND. Pins are
# 3.3V logic and should not be asked for more than about 16 mA.

from __future__ import annotations

import argparse
import time

from gpiozero import DigitalOutputDevice
from loguru import logger


def main() -> None:
    parser = argparse.ArgumentParser(description="Blink a GPIO pin for probing.")
    parser.add_argument("--pin", type=int, required=True, help="BCM GPIO number.")
    parser.add_argument("--hz", type=float, default=1.0, help="Toggle rate (default 1).")
    parser.add_argument("--seconds", type=float, default=30.0, help="How long to run.")
    args = parser.parse_args()

    half = 0.5 / args.hz
    pin = DigitalOutputDevice(args.pin, initial_value=False)
    logger.info("Blinking GPIO{} at {} Hz for {:.0f}s", args.pin, args.hz, args.seconds)
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            pin.on()
            time.sleep(half)
            pin.off()
            time.sleep(half)
    finally:
        pin.off()
        pin.close()
        logger.info("GPIO{} released (left low)", args.pin)


if __name__ == "__main__":
    main()
