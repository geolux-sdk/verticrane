# coding:UTF-8
# Resolve the HWT9037-485 serial port across Windows and Linux (Raspberry Pi).
#
# Resolution order (first match wins):
#   1. Explicit argument (e.g. a CLI --port value)
#   2. VERTICRANE_PORT environment variable
#   3. Auto-detected USB serial adapter (pyserial list_ports)
#   4. Platform default (Windows: COM11, Linux: /dev/ttyUSB0)
#
# The RS-485 dongle enumerates as /dev/ttyUSB* (FTDI/CH340) or /dev/ttyACM* on the
# Raspberry Pi, and as COMx on Windows; auto-detection keeps the scripts portable.

import os
import sys

from serial.tools import list_ports


ENV_VAR = "VERTICRANE_PORT"

# Used only when nothing else resolves a port.
_WINDOWS_DEFAULT = "COM11"
_LINUX_DEFAULT = "/dev/ttyUSB0"


def _platform_default():
    return _WINDOWS_DEFAULT if sys.platform.startswith("win") else _LINUX_DEFAULT


def autodetect_port():
    # Prefer a real USB-serial adapter (the RS-485 dongle). Such ports report a USB
    # VID; virtual/legacy ports leave it as None, so we use that to filter them out.
    candidates = [p.device for p in list_ports.comports() if p.vid is not None]
    if candidates:
        # Stable order so repeated runs pick the same adapter.
        candidates.sort()
        return candidates[0]
    return None


def resolve_port(explicit=None):
    # 1. Explicit CLI value.
    if explicit:
        return explicit
    # 2. Environment override.
    env = os.environ.get(ENV_VAR)
    if env:
        return env
    # 3. Auto-detected USB adapter.
    detected = autodetect_port()
    if detected:
        return detected
    # 4. Platform default.
    return _platform_default()


def add_port_argument(parser):
    # Shared --port option for the command-line tools.
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port (e.g. COM11 or /dev/ttyUSB0). "
             "Defaults to {0}, then auto-detection, then a platform default.".format(ENV_VAR),
    )
