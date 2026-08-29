# coding:UTF-8
# Self-check for the SCL3300 driver. Needs no hardware.
#
#   python test_scl3300.py
#
# Two things are worth pinning down before touching the bus. The frame encoding
# is checkable against the command words printed in the datasheet, so a wrong
# CRC polynomial or a wrong register address fails here rather than looking like
# a wiring fault. And the off-frame protocol -- the answer to request N arriving
# during frame N+1 -- is the part of this driver most likely to be quietly wrong,
# so it gets a fake bus that reproduces the delay exactly.

from __future__ import annotations

import sys

import scl3300 as s

_passed: int = 0
_failed: int = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print("  PASS  {0}".format(label))
    else:
        _failed += 1
        print("  FAIL  {0}{1}".format(label, "  <- " + detail if detail else ""))


# Command words as published by Murata. Anything this driver assembles for the
# same operation must come out bit-identical.
_DATASHEET = {
    "Read ACC_X": (0x040000F7, s.REG_ACC_X, 0, False),
    "Read ACC_Y": (0x080000FD, s.REG_ACC_Y, 0, False),
    "Read ACC_Z": (0x0C0000FB, s.REG_ACC_Z, 0, False),
    "Read STO": (0x100000E9, s.REG_STO, 0, False),
    "Read TEMP": (0x140000EF, s.REG_TEMP, 0, False),
    "Read STATUS": (0x180000E5, s.REG_STATUS, 0, False),
    "Read ERR_FLAG1": (0x1C0000E3, s.REG_ERR_FLAG1, 0, False),
    "Read ERR_FLAG2": (0x200000C1, s.REG_ERR_FLAG2, 0, False),
    "Read ANG_X": (0x240000C7, s.REG_ANG_X, 0, False),
    "Read ANG_Y": (0x280000CD, s.REG_ANG_Y, 0, False),
    "Read ANG_Z": (0x2C0000CB, s.REG_ANG_Z, 0, False),
    "Read CMD": (0x340000DF, s.REG_CMD, 0, False),
    "Read WHOAMI": (0x40000091, s.REG_WHOAMI, 0, False),
    "Read SERIAL1": (0x640000A7, s.REG_SERIAL1, 0, False),
    "Read SERIAL2": (0x680000AD, s.REG_SERIAL2, 0, False),
    "Read bank": (0x7C0000B3, s.REG_SELBANK, 0, False),
    "Enable angles": (0xB0001F6F, s.REG_ANG_CTRL, 0x001F, True),
    "Mode 1": (0xB400001F, s.REG_CMD, s.MODE_1, True),
    "Mode 2": (0xB4000102, s.REG_CMD, s.MODE_2, True),
    "Mode 3": (0xB4000225, s.REG_CMD, s.MODE_3, True),
    "Mode 4": (0xB4000338, s.REG_CMD, s.MODE_4, True),
    "Power down": (0xB400046B, s.REG_CMD, 0x0004, True),
    "SW reset": (0xB4002098, s.REG_CMD, 0x0020, True),
    "Switch bank 0": (0xFC000073, s.REG_SELBANK, 0, True),
}


def test_frame_encoding() -> None:
    bad = [name for name, (word, addr, data, write) in _DATASHEET.items()
           if s.request(addr, data, write=write) != word]
    check("every datasheet command word round-trips", not bad, ", ".join(bad))
    check("CRC covers the payload, not just the header",
          s.crc8(0x400000) != s.crc8(0x400001))
    check("a corrupted frame is rejected",
          s.crc8((0x240000C7 ^ 0x0100) >> 8) != (0x240000C7 & 0xFF))


def test_conversions() -> None:
    # 2^14 counts is 90 degrees, and the register is two's complement.
    check("+90 deg", abs(s.to_degrees(16384) - 90.0) < 1e-9)
    check("-90 deg", abs(s.to_degrees(-16384) + 90.0) < 1e-9)
    check("level reads zero", s.to_degrees(0) == 0.0)
    check("0xFFFF is -1 count, not 65535",
          s.Response(addr=s.REG_ANG_X, rs=s.RS_NORMAL, data=0xFFFF).signed == -1)
    check("0x8000 is the negative full scale",
          s.Response(addr=s.REG_ANG_X, rs=s.RS_NORMAL, data=0x8000).signed == -32768)
    check("room temperature decodes sanely",
          19.0 < s.to_celsius(int((25.0 + 273.0) * 18.9)) < 31.0,
          str(s.to_celsius(int((25.0 + 273.0) * 18.9))))
    check("RS text covers every code",
          all(s.rs_text(rs) != "unknown" for rs in (0, 1, 2, 3)))


class FakeBus:
    """A bus that answers one frame late, the way the real part does.

    Holds the pending request and serves its answer during the following
    transfer. If the driver ever stops accounting for that, the values come out
    shifted by one register and this fake is what catches it.
    """

    def __init__(self, registers: dict[int, int], rs: int = s.RS_NORMAL) -> None:
        self.registers = registers
        self.rs = rs
        self.pending: int | None = None
        self.frames = 0
        self.max_speed_hz = 0
        self.mode = 0
        self.bits_per_word = 8

    def xfer2(self, tx: list[int]) -> list[int]:
        self.frames += 1
        word = (tx[0] << 24) | (tx[1] << 16) | (tx[2] << 8) | tx[3]
        if s.crc8(word >> 8) != (word & 0xFF):
            raise AssertionError("driver sent a frame with a bad CRC")
        addr = (word >> 26) & 0x1F
        write = (word >> 31) & 1
        if write:
            self.registers[addr] = (word >> 8) & 0xFFFF

        if self.pending is None:
            reply = 0                       # nothing asked yet: undefined bus state
        else:
            reply = ((self.pending & 0x1F) << 26) | (self.rs << 24) \
                    | ((self.registers.get(self.pending, 0) & 0xFFFF) << 8)
            reply |= s.crc8(reply >> 8)
        self.pending = addr
        return [(reply >> 24) & 0xFF, (reply >> 16) & 0xFF,
                (reply >> 8) & 0xFF, reply & 0xFF]

    def close(self) -> None:
        pass


def fake_device(registers: dict[int, int], **kw) -> s.SCL3300:
    dev = s.SCL3300(**kw)
    dev._spi = FakeBus(registers)
    return dev


def test_off_frame_protocol() -> None:
    regs = {s.REG_ANG_X: 16384, s.REG_ANG_Y: 0xFFFF & (-16384 & 0xFFFF),
            s.REG_ANG_Z: 0, s.REG_WHOAMI: s.WHOAMI_EXPECTED}
    dev = fake_device(regs, inter_frame_us=0.0)

    check("a single read returns that register, not the one before",
          dev.read_register(s.REG_WHOAMI).data == s.WHOAMI_EXPECTED)

    x, y, z = dev.read_angles()
    check("a batched read keeps the answers in order",
          abs(x - 90.0) < 1e-9 and abs(y + 90.0) < 1e-9 and z == 0.0,
          "{:.3f} {:.3f} {:.3f}".format(x, y, z))

    before = dev._spi.frames
    dev.read_angles()
    check("three angles cost four frames, not six",
          dev._spi.frames - before == 4, str(dev._spi.frames - before))


def test_full_reading() -> None:
    regs = {
        s.REG_ANG_X: 1820, s.REG_ANG_Y: 0xFFFF & -910, s.REG_ANG_Z: 0,
        s.REG_ACC_X: 0, s.REG_ACC_Y: 0, s.REG_ACC_Z: 12000,
        s.REG_TEMP: int((25.0 + 273.0) * 18.9), s.REG_STATUS: 0,
        s.REG_WHOAMI: s.WHOAMI_EXPECTED,
    }
    dev = fake_device(regs, mode=s.MODE_4, inter_frame_us=0.0)
    r = dev.read()
    check("angle_x", abs(r.angle_x - 10.0) < 0.01, str(r.angle_x))
    check("angle_y", abs(r.angle_y + 5.0) < 0.01, str(r.angle_y))
    check("1 g on Z at mode 4 sensitivity", abs(r.acc_z - 1.0) < 1e-6, str(r.acc_z))
    check("mode 2 halves the sensitivity",
          abs(fake_device(regs, mode=s.MODE_2, inter_frame_us=0.0).read().acc_z - 4.0)
          < 1e-6)
    check("reading reports normal status", r.ok)


def test_startup_sequence() -> None:
    regs = {s.REG_WHOAMI: s.WHOAMI_EXPECTED, s.REG_STATUS: 0}
    dev = fake_device(regs, mode=s.MODE_3, inter_frame_us=0.0)
    dev.start(settle_s=0.0)
    check("start leaves the requested mode in CMD",
          regs.get(s.REG_CMD) == s.MODE_3, hex(regs.get(s.REG_CMD, -1)))
    check("start enables the angle outputs",
          regs.get(s.REG_ANG_CTRL) == 0x001F, hex(regs.get(s.REG_ANG_CTRL, -1)))

    wrong = {s.REG_WHOAMI: 0x00, s.REG_STATUS: 0}
    dev = fake_device(wrong, inter_frame_us=0.0)
    try:
        dev.start(settle_s=0.0)
        check("a wrong WHOAMI stops start()", False, "no exception")
    except s.SCL3300Error:
        check("a wrong WHOAMI stops start()", True)

    dev = fake_device({s.REG_WHOAMI: s.WHOAMI_EXPECTED}, inter_frame_us=0.0)
    dev._spi.rs = s.RS_ERROR
    try:
        dev.start(settle_s=0.0)
        check("a latched error flag stops start()", False, "no exception")
    except s.SCL3300Error:
        check("a latched error flag stops start()", True)


def test_bad_crc_is_raised() -> None:
    dev = fake_device({s.REG_WHOAMI: s.WHOAMI_EXPECTED}, inter_frame_us=0.0)
    original = dev._spi.xfer2

    def corrupting(tx: list[int]) -> list[int]:
        rx = original(tx)
        rx[3] ^= 0xFF                       # flip the CRC byte on the way back
        return rx

    dev._spi.xfer2 = corrupting
    try:
        dev.read_register(s.REG_WHOAMI)
        check("a corrupted response raises rather than returning garbage",
              False, "no exception")
    except s.SCL3300Error:
        check("a corrupted response raises rather than returning garbage", True)


def test_config_guards() -> None:
    try:
        s.SCL3300(mode=9)
        check("an unknown mode is refused up front", False, "no exception")
    except ValueError:
        check("an unknown mode is refused up front", True)
    try:
        s.SCL3300().read_register(s.REG_WHOAMI)
        check("reading before open() is refused", False, "no exception")
    except s.SCL3300Error:
        check("reading before open() is refused", True)
    check("CE1 is the default chip select", s.SCL3300()._device == 1)


def main() -> int:
    test_frame_encoding()
    test_conversions()
    test_off_frame_protocol()
    test_full_reading()
    test_startup_sequence()
    test_bad_crc_is_raised()
    test_config_guards()
    total: int = _passed + _failed
    print("\n{0}/{1} 통과".format(_passed, total))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
