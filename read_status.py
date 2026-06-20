# coding:UTF-8
# Read the current HWT9037-485 device status and print a decoded report.
#
# Also exposes read_device_status(), which returns the decoded configuration as a
# structured dict (reused by the dashboard's sensor-status panel).

from __future__ import annotations

import argparse
from typing import Any, Optional

from loguru import logger

import app_config  # noqa: F401  -- imported for its central loguru setup (LOG_LEVEL)
from hwt9037_485 import HWT9037_485
from port_config import add_port_argument, resolve_port


DEVICE_ADDR: int = 0x50
# Operational baud is 115200 (set and saved via configure_sensor.py), so try it first.
# 9600 is the factory default, kept as a fallback for an unconfigured unit.
CANDIDATE_BAUDS: list[int] = [115200, 9600]


# region Decode tables (from the Modbus protocol document)

BAUD_MAP: dict[int, str] = {
    0x01: "4800 bps", 0x02: "9600 bps", 0x03: "19200 bps", 0x04: "38400 bps",
    0x05: "57600 bps", 0x06: "115200 bps", 0x07: "230400 bps",
    0x08: "460800 bps", 0x09: "921600 bps",
}
BANDWIDTH_MAP: dict[int, str] = {0: "256 Hz", 1: "188 Hz", 2: "98 Hz", 3: "42 Hz", 4: "20 Hz", 5: "10 Hz", 6: "5 Hz"}
ACCRANGE_MAP: dict[int, str] = {0: "+/-2 g", 3: "+/-16 g"}
GYRORANGE_MAP: dict[int, str] = {3: "2000 deg/s"}
AXIS6_MAP: dict[int, str] = {0: "9-axis (absolute heading)", 1: "6-axis (relative heading)"}
ORIENT_MAP: dict[int, str] = {0: "horizontal", 1: "vertical"}
WORKMODE_MAP: dict[int, str] = {0: "normal data", 1: "peak-to-peak", 2: "seek zero bias", 3: "find scale factor"}
SLEEP_MAP: dict[int, str] = {0: "awake", 1: "sleep"}

# endregion


# Ordered status layout shared by the text report and the dashboard panel:
#   (group_key, group_title, [(field_key, field_label), ...])
# Live measurements are intentionally excluded -- this is configuration/status only.
STATUS_LAYOUT: list[tuple[str, str, list[tuple[str, str]]]] = [
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


def signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def decode_numberid(device: HWT9037_485) -> Optional[str]:
    # Device serial number lives in 0x7F~0x84, high byte first within each register.
    chars: list[str] = []
    for addr in range(0x7F, 0x85):
        raw: Optional[int] = device.registerData.get(addr)
        if raw is None:
            return None
        chars.append(chr((raw >> 8) & 0xFF))
        chars.append(chr(raw & 0xFF))
    return "".join(chars)


def fmt(label: str, value: Any) -> None:
    logger.info("  {:<22} {}", label, value)


# Read the configuration/status registers in a few contiguous blocks.
def read_config_registers(device: HWT9037_485) -> None:
    device.readReg(0x04, 1)    # BAUD
    device.readReg(0x0E, 1)    # WORKMODE
    device.readReg(0x1A, 1)    # IICADDR
    device.readReg(0x1F, 6)    # BANDWIDTH..AXIS6 (0x1F~0x24)
    device.readReg(0x25, 1)    # FILTK
    device.readReg(0x2A, 1)    # ACCFILT
    device.readReg(0x2E, 1)    # VERSION
    device.readReg(0x74, 1)    # MODDELAY
    device.readReg(0x7F, 6)    # NUMBERID1..6


def _fmt_enum(value: Optional[int], table: dict[int, str]) -> str:
    if value is None:
        return "-"
    return table.get(value, "0x{0:04X}".format(value))


# Decode the configuration registers into {field_key: display string}.
def decode_status(device: HWT9037_485) -> dict[str, str]:
    reg: dict[int, int] = device.registerData
    v: dict[str, str] = {}

    version: Optional[int] = reg.get(0x2E)
    v["fw_version"] = "0x{0:04X}".format(version) if version is not None else "-"
    v["serial_number"] = decode_numberid(device) or "-"
    addr: Optional[int] = reg.get(0x1A)
    v["modbus_addr"] = "0x{0:02X}".format(addr & 0xFF) if addr is not None else "-"
    baud: Optional[int] = reg.get(0x04)
    v["baud_rate"] = "{0} (0x{1:02X})".format(BAUD_MAP.get(baud, "unknown"), baud) if baud is not None else "-"
    moddelay: Optional[int] = reg.get(0x74)
    v["rs485_delay"] = "{0} us".format(moddelay) if moddelay is not None else "-"

    v["work_mode"] = _fmt_enum(reg.get(0x0E), WORKMODE_MAP)
    v["bandwidth"] = _fmt_enum(reg.get(0x1F), BANDWIDTH_MAP)
    v["gyro_range"] = _fmt_enum(reg.get(0x20), GYRORANGE_MAP)
    v["acc_range"] = _fmt_enum(reg.get(0x21), ACCRANGE_MAP)
    v["algorithm"] = _fmt_enum(reg.get(0x24), AXIS6_MAP)
    v["installation"] = _fmt_enum(reg.get(0x23), ORIENT_MAP)
    v["power_state"] = _fmt_enum(reg.get(0x22), SLEEP_MAP)

    filtk: Optional[int] = reg.get(0x25)
    v["filter_k"] = str(filtk) if filtk is not None else "-"
    accfilt: Optional[int] = reg.get(0x2A)
    v["acc_filter"] = str(accfilt) if accfilt is not None else "-"
    return v


# Probe the candidate baud rates and return the device opened at the one that answers.
def connectAutoBaud(port: str) -> tuple[Optional[HWT9037_485], Optional[int]]:
    for baud in CANDIDATE_BAUDS:
        device = HWT9037_485(port, baud, DEVICE_ADDR, lambda d: None)
        device.openDevice()
        if not device.isOpen:
            continue
        # VERSION is read-only and always present, so it confirms the link.
        device.readReg(0x2E, 1)
        if device.registerData.get(0x2E) is not None:
            logger.info("Connected at {} bps", baud)
            return device, baud
        logger.warning("No response at {} bps", baud)
        device.closeDevice()
    return None, None


# Connect, read the configuration registers, and return a structured status dict
# (or None if the device can't be reached). Opens and closes the serial port, so the
# port is free again for a measurement afterwards.
def read_device_status(port: Optional[str] = None) -> Optional[dict[str, Any]]:
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


def print_live_measurements(device: HWT9037_485) -> None:
    data: dict[str, float] = device.deviceData
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Read and decode HWT9037-485 device status.")
    add_port_argument(parser)
    args = parser.parse_args()
    port: str = resolve_port(args.port)

    device, baud = connectAutoBaud(port)
    if device is None:
        logger.error("Could not reach the device on {} at any candidate baud rate", port)
        return

    try:
        read_config_registers(device)
        # Live measurement blocks (these also decode into deviceData).
        device.readReg(0x34, 15)   # Acc + Gyro + Mag + Angle
        device.readReg(0x43, 1)    # Temperature
        device.readReg(0x51, 4)    # Quaternion

        values: dict[str, str] = decode_status(device)
        logger.info("=" * 56)
        logger.info("HWT9037-485 device status @ {} ({} bps)", port, baud)
        logger.info("=" * 56)
        for _gkey, gtitle, fields in STATUS_LAYOUT:
            logger.info("[{}]", gtitle)
            for fkey, flabel in fields:
                fmt(flabel, values.get(fkey, "-"))
        logger.info("[Live Measurements]")
        print_live_measurements(device)
        logger.info("=" * 56)
    finally:
        device.closeDevice()


if __name__ == "__main__":
    main()
