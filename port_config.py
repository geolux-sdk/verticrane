# coding:UTF-8
# Resolve the HWT9037-485 serial port across Windows and Linux (Raspberry Pi).
#
# Resolution order (first match wins):
#   1. Explicit argument (e.g. a CLI --port value)
#   2. VERTICRANE_PORT environment variable
#   3. Raspberry Pi on-board UART (/dev/serial0) -- the standard wiring
#   4. Auto-detected USB-RS485 adapter (pyserial list_ports)
#   5. Platform default (Windows: COM11, Linux: /dev/serial0)
#
# The standard hardware wires the target board straight to the Pi's on-board UART on
# GPIO14/15 (40-pin header pins 8/10), so that is tried first. It carries no USB VID and
# therefore never appears in the adapter scan, which is why it needs its own step rather
# than falling out of auto-detection.
#
# A USB-RS485 dongle (/dev/ttyUSB* or /dev/ttyACM* on Linux, COMx on Windows) still works
# for bench use and is picked up when no on-board UART is present. On a Pi that has both,
# name the dongle explicitly with --port or VERTICRANE_PORT: preferring the on-board UART
# keeps an unrelated USB serial device (a GPS, a debug cable) from being mistaken for the
# sensor.

from __future__ import annotations

import os
import sys
from typing import Optional

from serial.tools import list_ports


ENV_VAR = "VERTICRANE_PORT"

# Raspberry Pi on-board UART on GPIO14/15. serial0 is the Pi's stable alias for whichever
# UART the firmware routed to the header; ttyAMA0 is the PL011 it normally points at, kept
# as a fallback for images that do not create the alias. Requires enable_uart=1 in
# /boot/firmware/config.txt, and the serial console removed from cmdline.txt so nothing
# else holds the port -- see the Raspberry Pi setup section of README.md.
_ONBOARD_UARTS = ("/dev/serial0", "/dev/ttyAMA0")

# Used only when nothing else resolves a port. On Linux this is the standard wiring's
# port, so the resulting "no such file" points at the real problem: the UART is not
# enabled yet.
_WINDOWS_DEFAULT = "COM11"
_LINUX_DEFAULT = _ONBOARD_UARTS[0]


def _platform_default() -> str:
    return _WINDOWS_DEFAULT if sys.platform.startswith("win") else _LINUX_DEFAULT


def onboard_uart() -> Optional[str]:
    # The Pi's own UART, which the target board is wired to on the standard hardware.
    if sys.platform.startswith("win"):
        return None
    for device in _ONBOARD_UARTS:
        if os.path.exists(device):
            return device
    return None


def autodetect_port() -> Optional[str]:
    # Find a USB-serial adapter (the RS-485 dongle used on the bench). Such ports report
    # a USB VID; virtual/legacy ports and the on-board UART leave it as None, so we use
    # that to filter them out.
    candidates = [p.device for p in list_ports.comports() if p.vid is not None]
    if candidates:
        # Stable order so repeated runs pick the same adapter.
        candidates.sort()
        return candidates[0]
    return None


def resolve_port(explicit: Optional[str] = None) -> str:
    # 1. Explicit CLI value.
    if explicit:
        return explicit
    # 2. Environment override.
    env = os.environ.get(ENV_VAR)
    if env:
        return env
    # 3. Raspberry Pi on-board UART (standard wiring).
    onboard = onboard_uart()
    if onboard:
        return onboard
    # 4. Auto-detected USB adapter.
    detected = autodetect_port()
    if detected:
        return detected
    # 5. Platform default.
    return _platform_default()


def add_port_argument(parser) -> None:
    # Shared --port option for the command-line tools.
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port (e.g. /dev/serial0, COM11 or /dev/ttyUSB0). "
             "Defaults to {0}, then the Pi's on-board UART, then a USB-RS485 "
             "adapter, then a platform default.".format(ENV_VAR),
    )
