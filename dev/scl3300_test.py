# coding:UTF-8
# Bring-up test for the Murata SCL3300 on SPI0 CE1.
#
#   python scl3300_test.py                 identify, then stream at 10 Hz
#   python scl3300_test.py --seconds 5     stop after 5 s instead of Ctrl-C
#   python scl3300_test.py --rate 100      hammer the bus to expose interleaving
#   python scl3300_test.py --mode 3        MODE_3 (quiet) instead of MODE_4
#   python scl3300_test.py --probe         identify only, no streaming
#
# The order is on purpose. WHOAMI and the serial number come first because they
# are the only two answers whose correct value is known ahead of time: if the
# wiring or the chip select is wrong, they fail immediately and unambiguously,
# whereas an angle reading can look plausible while being garbage.
#
# The e-paper panel does not need to be running for any of this. If it is, run
# dev/eink_test.py in another shell at the same time -- that is the case worth
# proving, and --rate 100 is what makes a bus problem visible.

from __future__ import annotations

import argparse
import os
import sys
import time

from loguru import logger

# dev/ tools live one level down, so put the repo root on the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scl3300


def identify(dev: scl3300.SCL3300) -> None:
    who = dev.whoami()
    mark = "OK" if who == scl3300.WHOAMI_EXPECTED else "WRONG"
    print("  WHOAMI        0x{:02X}   ({}, expected 0x{:02X})".format(
        who, mark, scl3300.WHOAMI_EXPECTED))
    try:
        print("  serial        {}".format(dev.serial_number()))
    except scl3300.SCL3300Error as exc:
        # Reading the serial needs a bank switch; a failure here is worth seeing
        # but says nothing about whether the measurement path works.
        print("  serial        unavailable ({})".format(exc))

    st = dev.status()
    print("  RS            {} ({})".format(st["rs"], scl3300.rs_text(st["rs"])))
    print("  STATUS        0x{:04X}".format(st["status"]))
    print("  ERR_FLAG1/2   0x{:04X} / 0x{:04X}".format(st["err_flag1"], st["err_flag2"]))
    print("  temperature   {:.1f} C".format(dev.read_temperature()))


def stream(dev: scl3300.SCL3300, rate_hz: float, seconds: float) -> int:
    period = 1.0 / rate_hz
    deadline = time.monotonic() + seconds if seconds > 0 else float("inf")
    next_at = time.monotonic()
    samples = crc_errors = flagged = 0
    worst_gap = 0.0
    last = None

    print("\n  {:>8} {:>9} {:>9} {:>9} {:>8} {:>8} {:>8} {:>7}".format(
        "t(s)", "ANG_X", "ANG_Y", "ANG_Z", "acc_X", "acc_Y", "acc_Z", "degC"))
    started = time.monotonic()
    while time.monotonic() < deadline:
        now = time.monotonic()
        if last is not None:
            worst_gap = max(worst_gap, now - last)
        last = now
        try:
            r = dev.read()
        except scl3300.SCL3300Error as exc:
            # A CRC failure is the signal this whole exercise is looking for, so
            # count it and keep going rather than aborting the run.
            crc_errors += 1
            logger.error("frame {}: {}", samples, exc)
        else:
            samples += 1
            if not r.ok:
                flagged += 1
            print("  {:8.2f} {:9.3f} {:9.3f} {:9.3f} {:8.3f} {:8.3f} {:8.3f} {:7.1f}{}"
                  .format(now - started, r.angle_x, r.angle_y, r.angle_z,
                          r.acc_x, r.acc_y, r.acc_z, r.temperature,
                          "" if r.ok else "  <- RS=" + scl3300.rs_text(r.rs)))
        next_at += period
        gap = next_at - time.monotonic()
        if gap > 0:
            time.sleep(gap)
        else:
            next_at = time.monotonic()

    elapsed = time.monotonic() - started
    print("\n  {} samples in {:.1f} s ({:.1f} Hz actual)".format(
        samples, elapsed, samples / elapsed if elapsed else 0.0))
    print("  worst loop gap  {:.1f} ms  (asked for {:.1f} ms)".format(
        worst_gap * 1000.0, period * 1000.0))
    print("  CRC errors      {}".format(crc_errors))
    print("  frames with RS != normal  {}".format(flagged))
    return 1 if crc_errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SCL3300 bring-up on SPI0 CE1")
    ap.add_argument("--bus", type=int, default=0)
    ap.add_argument("--device", type=int, default=1, help="chip select (CE1)")
    ap.add_argument("--hz", type=int, default=2_000_000, help="SPI clock")
    ap.add_argument("--mode", type=int, default=4, choices=(1, 2, 3, 4))
    ap.add_argument("--rate", type=float, default=10.0, help="samples per second")
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = until Ctrl-C")
    ap.add_argument("--probe", action="store_true", help="identify only")
    ap.add_argument("--inter-frame-us", type=float, default=10.0,
                    help="CS high time between frames; 0 to rely on call overhead")
    args = ap.parse_args()

    dev = scl3300.SCL3300(bus=args.bus, device=args.device, hz=args.hz,
                          mode=args.mode - 1, inter_frame_us=args.inter_frame_us)
    print("SCL3300 on /dev/spidev{}.{} at {} Hz, mode {}".format(
        args.bus, args.device, args.hz, args.mode))
    try:
        dev.open()
    except Exception as exc:                              # noqa: BLE001
        logger.error("cannot open the bus: {}", exc)
        logger.error("check dtparam=spi=on and that the user is in the spi group")
        return 2

    try:
        dev.start()
        identify(dev)
        if args.probe:
            return 0
        return stream(dev, args.rate, args.seconds)
    except scl3300.SCL3300Error as exc:
        logger.error("{}", exc)
        return 1
    except KeyboardInterrupt:
        print("\n  stopped")
        return 0
    finally:
        dev.close()


if __name__ == "__main__":
    sys.exit(main())
