# coding:UTF-8
# Configure the HWT9037-485 for tilt measurement (current/flat mounting).
#
# Applies and saves:
#   - AXIS6 (0x24) = 0x01 : 6-axis algorithm (accel+gyro, drop magnetometer)
# Leaves unchanged (already correct for flat / Z-up mounting):
#   - ORIENT (0x23) = 0x00 : horizontal installation
#
# The write is persisted (survives power cycle). Re-run with AXIS6=0x00 to revert.

import argparse
import time

from hwt9037_485 import HWT9037_485
from port_config import add_port_argument, resolve_port


# Resolved from --port / VERTICRANE_PORT / auto-detect in main().
PORT_NAME = None
DEVICE_ADDR = 0x50
CANDIDATE_BAUDS = [9600, 115200]

AXIS6_MAP = {0: "9-axis (mag absolute heading)", 1: "6-axis (gyro relative heading)"}
ORIENT_MAP = {0: "horizontal (Z up)", 1: "vertical (Y up)"}


def connectAutoBaud():
    for baud in CANDIDATE_BAUDS:
        device = HWT9037_485(PORT_NAME, baud, DEVICE_ADDR, lambda d: None)
        device.openDevice()
        if not device.isOpen:
            continue
        device.readReg(0x2E, 1)
        if device.registerData.get(0x2E) is not None:
            print("Connected at {0} bps".format(baud))
            return device
        device.closeDevice()
    return None


def _hex(v):
    # Tolerate a failed read (None) instead of crashing the status print.
    return "0x{0:04X}".format(v) if v is not None else "None (read failed)"


def show(device, label):
    device.readReg(0x23, 1)  # ORIENT
    device.readReg(0x24, 1)  # AXIS6
    orient = device.registerData.get(0x23)
    axis6 = device.registerData.get(0x24)
    print("{0}: ORIENT={1} ({2}), AXIS6={3} ({4})".format(
        label, _hex(orient), ORIENT_MAP.get(orient, "?"),
        _hex(axis6), AXIS6_MAP.get(axis6, "?")))
    return orient, axis6


def main():
    global PORT_NAME
    parser = argparse.ArgumentParser(description="Configure HWT9037-485 for tilt measurement (6-axis).")
    add_port_argument(parser)
    args = parser.parse_args()
    PORT_NAME = resolve_port(args.port)

    device = connectAutoBaud()
    if device is None:
        print("Could not reach the device on {0}".format(PORT_NAME))
        return

    try:
        print("--- Before ---")
        show(device, "current")

        # Switch to 6-axis and persist. ORIENT stays horizontal (current mounting).
        print("--- Applying AXIS6 = 0x01 (6-axis), saved ---")
        device.writeReg(0x24, 0x0001, save=True)
        time.sleep(0.2)

        print("--- After ---")
        _, axis6 = show(device, "current")

        if axis6 == 0x0001:
            print("OK: sensor is now in 6-axis mode for tilt measurement.")
        else:
            print("WARNING: AXIS6 did not read back as 0x0001 (got {0}).".format(_hex(axis6)))
    finally:
        device.closeDevice()


if __name__ == "__main__":
    main()
