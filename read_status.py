# coding:UTF-8
# Read the current HWT9037-485 device status and print a decoded report.
#
# Also exposes read_device_status(), which returns the decoded configuration as a
# structured dict (reused by the dashboard's sensor-status panel).

import argparse

from hwt9037_485 import HWT9037_485
from port_config import add_port_argument, resolve_port


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


# Ordered status layout shared by the text report and the dashboard panel:
#   (group_key, group_title, [(field_key, field_label), ...])
# Live measurements are intentionally excluded -- this is configuration/status only.
STATUS_LAYOUT = [
    ("identity", "Identity & Communication", [
        ("fw_version", "Firmware version"),
        ("serial_number", "Serial number"),
        ("modbus_addr", "Modbus address"),
        ("baud_rate", "Baud rate"),
        ("rs485_delay", "RS485 resp. delay"),
    ]),
    ("mode", "Operating Mode", [
        ("work_mode", "Work mode"),
        ("bandwidth", "Bandwidth"),
        ("gyro_range", "Gyroscope range"),
        ("acc_range", "Acceleration range"),
        ("algorithm", "Algorithm"),
        ("installation", "Installation"),
        ("power_state", "Power state"),
    ]),
    ("filters", "Filters", [
        ("filter_k", "Dynamic filter (K)"),
        ("acc_filter", "Acceleration filter"),
    ]),
]


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


# Read the configuration/status registers in a few contiguous blocks.
def read_config_registers(device):
    device.readReg(0x04, 1)    # BAUD
    device.readReg(0x0E, 1)    # WORKMODE
    device.readReg(0x1A, 1)    # IICADDR
    device.readReg(0x1F, 6)    # BANDWIDTH..AXIS6 (0x1F~0x24)
    device.readReg(0x25, 1)    # FILTK
    device.readReg(0x2A, 1)    # ACCFILT
    device.readReg(0x2E, 1)    # VERSION
    device.readReg(0x74, 1)    # MODDELAY
    device.readReg(0x7F, 6)    # NUMBERID1..6


def _fmt_enum(value, table):
    if value is None:
        return "-"
    return table.get(value, "0x{0:04X}".format(value))


# Decode the configuration registers into {field_key: display string}.
def decode_status(device):
    reg = device.registerData
    v = {}

    version = reg.get(0x2E)
    v["fw_version"] = "0x{0:04X}".format(version) if version is not None else "-"
    v["serial_number"] = decode_numberid(device) or "-"
    addr = reg.get(0x1A)
    v["modbus_addr"] = "0x{0:02X}".format(addr & 0xFF) if addr is not None else "-"
    baud = reg.get(0x04)
    v["baud_rate"] = "{0} (0x{1:02X})".format(BAUD_MAP.get(baud, "unknown"), baud) if baud is not None else "-"
    moddelay = reg.get(0x74)
    v["rs485_delay"] = "{0} us".format(moddelay) if moddelay is not None else "-"

    v["work_mode"] = _fmt_enum(reg.get(0x0E), WORKMODE_MAP)
    v["bandwidth"] = _fmt_enum(reg.get(0x1F), BANDWIDTH_MAP)
    v["gyro_range"] = _fmt_enum(reg.get(0x20), GYRORANGE_MAP)
    v["acc_range"] = _fmt_enum(reg.get(0x21), ACCRANGE_MAP)
    v["algorithm"] = _fmt_enum(reg.get(0x24), AXIS6_MAP)
    v["installation"] = _fmt_enum(reg.get(0x23), ORIENT_MAP)
    v["power_state"] = _fmt_enum(reg.get(0x22), SLEEP_MAP)

    filtk = reg.get(0x25)
    v["filter_k"] = str(filtk) if filtk is not None else "-"
    accfilt = reg.get(0x2A)
    v["acc_filter"] = str(accfilt) if accfilt is not None else "-"
    return v


# Probe the candidate baud rates and return the device opened at the one that answers.
def connectAutoBaud(port):
    for baud in CANDIDATE_BAUDS:
        device = HWT9037_485(port, baud, DEVICE_ADDR, lambda d: None)
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


# Connect, read the configuration registers, and return a structured status dict
# (or None if the device can't be reached). Opens and closes the serial port, so the
# port is free again for a measurement afterwards.
def read_device_status(port=None):
    port = resolve_port(port)
    device, baud = connectAutoBaud(port)
    if device is None:
        return None
    try:
        device.verbose = False
        read_config_registers(device)
        return {"port": port, "baud": baud, "values": decode_status(device)}
    finally:
        device.closeDevice()


def print_live_measurements(device):
    data = device.deviceData
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


def main():
    parser = argparse.ArgumentParser(description="Read and decode HWT9037-485 device status.")
    add_port_argument(parser)
    args = parser.parse_args()
    port = resolve_port(args.port)

    device, baud = connectAutoBaud(port)
    if device is None:
        print("Could not reach the device on {0} at any candidate baud rate".format(port))
        return

    try:
        read_config_registers(device)
        # Live measurement blocks (these also decode into deviceData).
        device.readReg(0x34, 15)   # Acc + Gyro + Mag + Angle
        device.readReg(0x43, 1)    # Temperature
        device.readReg(0x51, 4)    # Quaternion

        values = decode_status(device)
        print("=" * 56)
        print("HWT9037-485 device status @ {0} ({1} bps)".format(port, baud))
        print("=" * 56)
        for _gkey, gtitle, fields in STATUS_LAYOUT:
            print("[{0}]".format(gtitle))
            for fkey, flabel in fields:
                fmt(flabel, values.get(fkey, "-"))
        print("[Live Measurements]")
        print_live_measurements(device)
        print("=" * 56)
    finally:
        device.closeDevice()


if __name__ == "__main__":
    main()
