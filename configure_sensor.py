# coding:UTF-8
# Configure the HWT9037-485 for tilt measurement (current/flat mounting).
#
# Applies and saves:
#   - AXIS6 (0x24) = 0x01 : 6-axis algorithm (accel+gyro, drop magnetometer)
#   - optionally BAUD (0x04) when --baud is given
# Leaves unchanged (already correct for flat / Z-up mounting):
#   - ORIENT (0x23) = 0x00 : horizontal installation
#
# The writes are persisted (survive power cycle). Re-run with AXIS6=0x00 to revert.
#
#   python configure_sensor.py                 # set 6-axis only
#   python configure_sensor.py --baud 115200   # set 6-axis and switch baud to 115200

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

# BAUD register (0x04): baud rate -> register code (from the Modbus protocol document).
BAUD_CODES = {
    4800: 0x01, 9600: 0x02, 19200: 0x03, 38400: 0x04, 57600: 0x05,
    115200: 0x06, 230400: 0x07, 460800: 0x08, 921600: 0x09,
}
BAUD_BY_CODE = {code: baud for baud, code in BAUD_CODES.items()}


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


def set_baud(device, target_baud):
    # Set and save the BAUD register, then reopen the link at the new rate.
    # Run this LAST: after the save the device talks at the new baud, so the old
    # connection is no longer usable. Returns the (re)connected device handle.
    target_code = BAUD_CODES[target_baud]
    device.readReg(0x04, 1)
    current_code = device.registerData.get(0x04)
    current_baud = BAUD_BY_CODE.get(current_code)
    print("--- Baud: current {0}, target {1} bps ---".format(
        "{0} bps".format(current_baud) if current_baud else _hex(current_code), target_baud))

    if current_code == target_code:
        print("OK: baud already at {0} bps; nothing to change.".format(target_baud))
        return device

    print("Setting BAUD (0x04) = {0} ({1} bps), saved...".format(_hex(target_code), target_baud))
    device.writeReg(0x04, target_code, save=True)
    device.closeDevice()
    time.sleep(0.5)

    # Reconnect at the new rate to confirm the switch took effect.
    newdev = HWT9037_485(PORT_NAME, target_baud, DEVICE_ADDR, lambda d: None)
    newdev.openDevice()
    newdev.readReg(0x2E, 1)  # VERSION confirms the link
    if newdev.registerData.get(0x2E) is not None:
        print("OK: now communicating at {0} bps.".format(target_baud))
    else:
        print("WARNING: could not confirm the device at {0} bps; a power cycle may help.".format(target_baud))
    return newdev


def main():
    global PORT_NAME
    parser = argparse.ArgumentParser(description="Configure HWT9037-485 for tilt measurement (6-axis).")
    add_port_argument(parser)
    parser.add_argument("--baud", type=int, choices=sorted(BAUD_CODES), default=None,
                        help="Also set and save the device baud rate (e.g. 9600, 115200).")
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

        # Change the baud rate last, since the link must switch to the new rate after.
        if args.baud is not None:
            device = set_baud(device, args.baud)
    finally:
        if device is not None:
            device.closeDevice()


if __name__ == "__main__":
    main()
