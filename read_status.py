# coding:UTF-8
# Read the current HWT9037-485 device status and print a decoded report.

import argparse
import time

from hwt9037_485 import HWT9037_485
from port_config import add_port_argument, resolve_port


# Resolved from --port / VERTICRANE_PORT / auto-detect in main().
PORT_NAME = None
DEVICE_ADDR = 0x50
# The device powers on at 9600 bps (factory default) but can be switched and saved to a
# higher rate. Probe the likely rates and use whichever one answers.
CANDIDATE_BAUDS = [9600, 115200]


# region Decode tables (from the Modbus protocol document)

BAUD_MAP = {
    0x01: "4800 bps", 0x02: "9600 bps", 0x03: "19200 bps", 0x04: "38400 bps",
    0x05: "57600 bps", 0x06: "115200 bps", 0x07: "230400 bps",
    0x08: "460800 bps", 0x09: "921600 bps",
}
BANDWIDTH_MAP = {0: "256 Hz", 1: "188 Hz", 2: "98 Hz", 3: "42 Hz", 4: "20 Hz", 5: "10 Hz", 6: "5 Hz"}
ACCRANGE_MAP = {0: "+/-2 g", 3: "+/-16 g"}
GYRORANGE_MAP = {3: "2000 deg/s"}
AXIS6_MAP = {0: "9-axis (absolute heading)", 1: "6-axis (relative heading)"}
ORIENT_MAP = {0: "horizontal", 1: "vertical"}
WORKMODE_MAP = {0: "normal data", 1: "peak-to-peak", 2: "seek zero bias", 3: "find scale factor"}
SLEEP_MAP = {0: "awake", 1: "sleep"}

# endregion


def signed16(value):
    return value - 0x10000 if value & 0x8000 else value


def decode_numberid(device):
    # Device serial number lives in 0x7F~0x84, high byte first within each register.
    chars = []
    for addr in range(0x7F, 0x85):
        raw = device.registerData.get(addr)
        if raw is None:
            return None
        chars.append(chr((raw >> 8) & 0xFF))
        chars.append(chr(raw & 0xFF))
    return "".join(chars)


def fmt(label, value):
    print("  {0:<22} {1}".format(label, value))


def printStatus(device, baud):
    reg = device.registerData
    data = device.deviceData

    print("=" * 56)
    print("HWT9037-485 device status @ {0} ({1} bps)".format(PORT_NAME, baud))
    print("=" * 56)

    # --- Identity & communication ---
    print("[Identity & Communication]")
    version = reg.get(0x2E)
    fmt("Firmware version", "0x{0:04X}".format(version) if version is not None else "-")
    fmt("Serial number", decode_numberid(device) or "-")
    addr = reg.get(0x1A)
    fmt("Modbus address", "0x{0:02X}".format(addr & 0xFF) if addr is not None else "-")
    baud = reg.get(0x04)
    fmt("Baud rate", "{0} (0x{1:02X})".format(BAUD_MAP.get(baud, "unknown"), baud) if baud is not None else "-")
    moddelay = reg.get(0x74)
    fmt("RS485 resp. delay", "{0} us".format(moddelay) if moddelay is not None else "-")

    # --- Operating mode ---
    print("[Operating Mode]")
    workmode = reg.get(0x0E)
    fmt("Work mode", WORKMODE_MAP.get(workmode, "0x{0:04X}".format(workmode)) if workmode is not None else "-")
    bw = reg.get(0x1F)
    fmt("Bandwidth", BANDWIDTH_MAP.get(bw, "0x{0:04X}".format(bw)) if bw is not None else "-")
    gr = reg.get(0x20)
    fmt("Gyroscope range", GYRORANGE_MAP.get(gr, "0x{0:04X}".format(gr)) if gr is not None else "-")
    ar = reg.get(0x21)
    fmt("Acceleration range", ACCRANGE_MAP.get(ar, "0x{0:04X}".format(ar)) if ar is not None else "-")
    axis6 = reg.get(0x24)
    fmt("Algorithm", AXIS6_MAP.get(axis6, "0x{0:04X}".format(axis6)) if axis6 is not None else "-")
    orient = reg.get(0x23)
    fmt("Installation", ORIENT_MAP.get(orient, "0x{0:04X}".format(orient)) if orient is not None else "-")
    sleep = reg.get(0x22)
    fmt("Power state", SLEEP_MAP.get(sleep, "0x{0:04X}".format(sleep)) if sleep is not None else "-")

    # --- Filters ---
    print("[Filters]")
    filtk = reg.get(0x25)
    fmt("Dynamic filter (K)", filtk if filtk is not None else "-")
    accfilt = reg.get(0x2A)
    fmt("Acceleration filter", accfilt if accfilt is not None else "-")

    # --- Live measurements ---
    print("[Live Measurements]")
    fmt("Acceleration (g)", "X={0}, Y={1}, Z={2}".format(
        data.get("AccX", "-"), data.get("AccY", "-"), data.get("AccZ", "-")))
    fmt("Angular vel. (deg/s)", "X={0}, Y={1}, Z={2}".format(
        data.get("AsX", "-"), data.get("AsY", "-"), data.get("AsZ", "-")))
    fmt("Magnetic field", "X={0}, Y={1}, Z={2}".format(
        data.get("HX", "-"), data.get("HY", "-"), data.get("HZ", "-")))
    fmt("Angle (deg)", "Roll={0}, Pitch={1}, Yaw={2}".format(
        data.get("AngX", "-"), data.get("AngY", "-"), data.get("AngZ", "-")))
    fmt("Temperature (C)", data.get("Temp", "-"))
    fmt("Quaternion", "q0={0}, q1={1}, q2={2}, q3={3}".format(
        data.get("q0", "-"), data.get("q1", "-"), data.get("q2", "-"), data.get("q3", "-")))
    print("=" * 56)


# Probe the candidate baud rates and return the device opened at the one that answers.
def connectAutoBaud():
    for baud in CANDIDATE_BAUDS:
        device = HWT9037_485(PORT_NAME, baud, DEVICE_ADDR, lambda d: None)
        device.openDevice()
        if not device.isOpen:
            continue
        # VERSION is read-only and always present, so it confirms the link.
        device.readReg(0x2E, 1)
        if device.registerData.get(0x2E) is not None:
            print("Connected at {0} bps".format(baud))
            return device, baud
        print("No response at {0} bps".format(baud))
        device.closeDevice()
    return None, None


def main():
    global PORT_NAME
    parser = argparse.ArgumentParser(description="Read and decode HWT9037-485 device status.")
    add_port_argument(parser)
    args = parser.parse_args()
    PORT_NAME = resolve_port(args.port)

    device, baud = connectAutoBaud()
    if device is None:
        print("Could not reach the device on {0} at any candidate baud rate".format(PORT_NAME))
        return

    try:
        # Configuration registers (read in a few contiguous blocks).
        device.readReg(0x04, 1)    # BAUD
        device.readReg(0x0E, 1)    # WORKMODE
        device.readReg(0x1A, 1)    # IICADDR
        device.readReg(0x1F, 6)    # BANDWIDTH..AXIS6 (0x1F~0x24)
        device.readReg(0x25, 1)    # FILTK
        device.readReg(0x2A, 1)    # ACCFILT
        device.readReg(0x2E, 1)    # VERSION
        device.readReg(0x74, 1)    # MODDELAY
        device.readReg(0x7F, 6)    # NUMBERID1..6

        # Live measurement blocks (these also decode into deviceData).
        device.readReg(0x34, 15)   # Acc + Gyro + Mag + Angle
        device.readReg(0x43, 1)    # Temperature
        device.readReg(0x51, 4)    # Quaternion

        printStatus(device, baud)
    finally:
        device.closeDevice()


if __name__ == "__main__":
    main()
