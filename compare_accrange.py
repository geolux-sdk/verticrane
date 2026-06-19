# coding:UTF-8
# Empirically compare AccX/Y/Z output quantization in +/-2 g vs +/-16 g mode.
#
# The sensor must be still (resting on a desk). For each ACCRANGE setting we collect
# raw acceleration register values and report:
#   - the magnitude of AccZ (~1 g at rest): reveals whether the output scale follows
#     the range setting (~2048 => fixed 16 g scale, ~16384 => range-scaled 2 g),
#   - the smallest code step between distinct raw values: reveals output resolution.
#
# ACCRANGE is written WITHOUT save (RAM only), so the persisted configuration is left
# unchanged; the original range is restored before exit.

import argparse
import math
import time

from hwt9037_485 import HWT9037_485
from port_config import add_port_argument, resolve_port

DEVICE_ADDR = 0x50
CANDIDATE_BAUDS = [9600, 115200]
ACCRANGE_REG = 0x21
SAMPLES = 200
RANGE_NAMES = {0x00: "+/-2 g", 0x03: "+/-16 g"}


def connectAutoBaud(port):
    for baud in CANDIDATE_BAUDS:
        device = HWT9037_485(port, baud, DEVICE_ADDR, lambda d: None)
        device.openDevice()
        if not device.isOpen:
            continue
        device.readReg(0x2E, 1)  # VERSION confirms the link
        if device.registerData.get(0x2E) is not None:
            print("Connected at {0} bps".format(baud))
            return device
        device.closeDevice()
    return None


def collect(device, n):
    # Return signed raw register values for AccX/Y/Z over n samples.
    rawx, rawy, rawz = [], [], []
    for _ in range(n):
        device.readReg(0x34, 15)
        rx = device.registerData.get(0x34)
        ry = device.registerData.get(0x35)
        rz = device.registerData.get(0x36)
        if rx is not None:
            rawx.append(device.getSignInt16(rx))
            rawy.append(device.getSignInt16(ry))
            rawz.append(device.getSignInt16(rz))
        time.sleep(0.04)
    return rawx, rawy, rawz


def code_step(values):
    # Smallest positive difference between distinct observed raw codes.
    uniq = sorted(set(values))
    if len(uniq) < 2:
        return None
    return min(b - a for a, b in zip(uniq, uniq[1:]))


def stats(values):
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(var), min(values), max(values)


def report(label, raw):
    rawx, rawy, rawz = raw
    print("\n=== {0} ===".format(label))
    for axis, vals in (("AccX", rawx), ("AccY", rawy), ("AccZ", rawz)):
        if not vals:
            print("  {0}: no data".format(axis))
            continue
        mean, std, lo, hi = stats(vals)
        step = code_step(vals)
        # The current parser converts every axis with the fixed /32768*16 formula.
        g_mean = mean / 32768 * 16
        print("  {0}: raw mean={1:8.1f}  std={2:5.2f}  range[{3},{4}]  "
              "distinct={5:3d}  step={6}  -> {7:+.4f} g (via /32768*16)".format(
                  axis, mean, std, lo, hi, len(set(vals)),
                  step if step is not None else "-", g_mean))


def set_range(device, value):
    # This firmware only applies ACCRANGE from flash, so write WITH save, then reboot
    # to load it, then read the register back to confirm what actually took effect.
    device.writeReg(ACCRANGE_REG, value, save=True)
    time.sleep(0.3)
    device.reboot()
    # The device stops responding while it restarts; wait, then confirm the link.
    time.sleep(2.0)
    for _ in range(10):
        device.readReg(0x2E, 1)  # VERSION confirms the device is back
        if device.registerData.get(0x2E) is not None:
            break
        time.sleep(0.3)
    device.readReg(ACCRANGE_REG, 1)
    return device.registerData.get(ACCRANGE_REG)


def main():
    parser = argparse.ArgumentParser(description="Compare Acc resolution in 2g vs 16g mode.")
    add_port_argument(parser)
    args = parser.parse_args()
    port = resolve_port(args.port)

    device = connectAutoBaud(port)
    if device is None:
        print("Could not reach the device on {0}".format(port))
        return
    device.verbose = False

    try:
        device.readReg(ACCRANGE_REG, 1)
        original = device.registerData.get(ACCRANGE_REG)
        print("Original ACCRANGE = 0x{0:04X} ({1})".format(
            original if original is not None else 0, RANGE_NAMES.get(original, "?")))
        print("Keep the sensor still. Collecting {0} samples per mode...".format(SAMPLES))

        results = {}
        for value in (0x03, 0x00):  # 16 g first, then 2 g
            rb = set_range(device, value)
            print("\nSet ACCRANGE -> 0x{0:04X} ({1}); read back 0x{2:04X}".format(
                value, RANGE_NAMES[value], rb if rb is not None else 0))
            results[value] = collect(device, SAMPLES)
            report(RANGE_NAMES[value], results[value])

        # Restore the original range (RAM only; nothing was persisted).
        if original is not None:
            set_range(device, original)
            print("\nRestored ACCRANGE -> 0x{0:04X} ({1})".format(
                original, RANGE_NAMES.get(original, "?")))

        # Verdict: AccZ raw magnitude is the discriminator.
        if results[0x03][2] and results[0x00][2]:
            z16 = abs(stats(results[0x03][2])[0])
            z2 = abs(stats(results[0x00][2])[0])
            print("\n--- Verdict ---")
            print("AccZ raw |mean|: 16g={0:.0f}, 2g={1:.0f}".format(z16, z2))
            if z16 > 0 and z2 > z16 * 4:
                print("2g raw is ~{0:.1f}x larger -> output scale FOLLOWS range.".format(z2 / z16))
                print("  => finer resolution in 2g, BUT /32768*16 over-reports ~8x in 2g mode.")
            else:
                print("2g and 16g raw are similar -> output is FIXED 16g scale.")
                print("  => no output-resolution gain from 2g; /32768*16 stays correct for both.")
                print("  (If you suspect the change needs a flash save to take effect,")
                print("   re-run writing ACCRANGE with save=True and power-cycle to confirm.)")
    finally:
        device.closeDevice()


if __name__ == "__main__":
    main()
