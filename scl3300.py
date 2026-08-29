# coding:UTF-8
# Driver for the Murata SCL3300 3-axis inclinometer on the Raspberry Pi's SPI0
# bus, chip select 1.
#
# The e-paper panel already owns CE0 (see gdey0154d67.py). This part shares
# SCLK/MOSI with it and takes CE1; nothing else is added to the header:
#
#   MOSI -> GPIO10 (pin 19)   shared with the panel
#   MISO -> GPIO9  (pin 21)   free -- the panel is write-only and has no MISO
#   SCLK -> GPIO11 (pin 23)   shared with the panel
#   CSB  -> GPIO7 / CE1 (pin 26)
#   VDD, VDDIO -> 3.3V (pin 1)          GND -> GND (pin 9)
#
# Both parts are SPI mode 0 and the panel never drives MISO, so the bus needs no
# arbitration beyond the kernel's own per-controller lock. Keep this SpiDev
# instance separate from the panel's -- one object per chip select, never shared.
#
# Two things about the SCL3300 protocol drive the shape of this module:
#
#   * Every exchange is exactly 32 bits: RW(1) ADDR(5) RS(2) DATA(16) CRC(8),
#     MSB first, and CS must rise between frames.
#   * The device answers off-frame. The response to request N arrives during
#     frame N+1, so a read is never a single transfer. _exchange() hides this by
#     appending a flush frame and discarding the first response.

from __future__ import annotations

import time
from typing import Iterable, NamedTuple

from loguru import logger

# --- register map ---------------------------------------------------------
REG_ACC_X = 0x01
REG_ACC_Y = 0x02
REG_ACC_Z = 0x03
REG_STO = 0x04
REG_TEMP = 0x05
REG_STATUS = 0x06
REG_ERR_FLAG1 = 0x07
REG_ERR_FLAG2 = 0x08
REG_ANG_X = 0x09
REG_ANG_Y = 0x0A
REG_ANG_Z = 0x0B
REG_ANG_CTRL = 0x0C
REG_CMD = 0x0D          # write: mode / reset / power state; read: current mode
REG_WHOAMI = 0x10
REG_SERIAL1 = 0x19
REG_SERIAL2 = 0x1A
REG_SELBANK = 0x1F

# --- measurement modes ----------------------------------------------------
# The value written to REG_CMD, and what it buys. Mode 4 is the widest range;
# mode 3 is the quietest. Modes 3 and 4 need a longer settle after the switch.
MODE_1 = 0              # +/-1.2 g, 40 Hz low-pass
MODE_2 = 1              # +/-2.4 g, 70 Hz low-pass
MODE_3 = 2              # +/-1.2 g, 10 Hz low-pass, low noise
MODE_4 = 3              # +/-3.6 g, 10 Hz low-pass, low noise

# LSB per g, per mode.
_ACC_SENSITIVITY = {MODE_1: 6000.0, MODE_2: 3000.0, MODE_3: 12000.0, MODE_4: 12000.0}

_CMD_SW_RESET = 0x0020
_CMD_POWER_DOWN = 0x0004
_ANG_CTRL_ENABLE = 0x001F

WHOAMI_EXPECTED = 0xC1

# Return-status field, bits [25:24] of every response.
RS_STARTUP = 0b00
RS_NORMAL = 0b01
RS_RESERVED = 0b10      # not used by the SCL3300; treated as suspect
RS_ERROR = 0b11
_RS_TEXT = {
    RS_STARTUP: "startup in progress",
    RS_NORMAL: "normal",
    RS_RESERVED: "reserved",
    RS_ERROR: "error flag set",
}


class SCL3300Error(RuntimeError):
    """A frame came back malformed, or the device reported a fault."""


def crc8(data24: int) -> int:
    """CRC-8 over the top 24 bits of a frame: poly 0x1D, init 0xFF, final XOR 0xFF.

    Checked against the command words published in the datasheet before this
    driver was written; do not "simplify" the constants.
    """
    crc = 0xFF
    for i in range(23, -1, -1):
        bit = (data24 >> i) & 1
        if ((crc >> 7) & 1) ^ bit:
            crc = ((crc << 1) & 0xFF) ^ 0x1D
        else:
            crc = (crc << 1) & 0xFF
    return crc ^ 0xFF


def request(addr: int, data: int = 0, write: bool = False) -> int:
    """Assemble a 32-bit request word with its CRC."""
    word = ((1 if write else 0) << 31) | ((addr & 0x1F) << 26) | ((data & 0xFFFF) << 8)
    return word | crc8(word >> 8)


def _signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def to_degrees(raw_signed: int) -> float:
    # The angle registers are scaled so that 2^14 counts is 90 degrees.
    return raw_signed * 90.0 / 16384.0


def to_celsius(raw: int) -> float:
    return -273.0 + raw / 18.9


def rs_text(rs: int) -> str:
    return _RS_TEXT.get(rs, "unknown")


class Response(NamedTuple):
    addr: int
    rs: int
    data: int

    @property
    def ok(self) -> bool:
        return self.rs == RS_NORMAL

    @property
    def signed(self) -> int:
        return _signed16(self.data)


class Reading(NamedTuple):
    """One sample. Angles in degrees, acceleration in g, temperature in Celsius."""
    angle_x: float
    angle_y: float
    angle_z: float
    acc_x: float
    acc_y: float
    acc_z: float
    temperature: float
    rs: int

    @property
    def ok(self) -> bool:
        return self.rs == RS_NORMAL


class SCL3300:
    def __init__(self, bus: int = 0, device: int = 1, hz: int = 2_000_000,
                 mode: int = MODE_4, inter_frame_us: float = 10.0) -> None:
        if mode not in _ACC_SENSITIVITY:
            raise ValueError("mode must be one of MODE_1..MODE_4")
        self._bus, self._device, self._hz = bus, device, hz
        self._mode = mode
        # CS must stay high for a short spell between frames. Python's per-ioctl
        # overhead alone usually covers it, but bring-up is not the place to rely
        # on that, so spin briefly. Set to 0 once the wiring is proven.
        self._inter_frame_s = inter_frame_us / 1e6
        self._spi = None

    @property
    def mode(self) -> int:
        return self._mode

    # --- bus ---------------------------------------------------------------

    def open(self) -> None:
        # Imported here so this module stays importable off the Pi.
        import spidev

        self._spi = spidev.SpiDev()
        self._spi.open(self._bus, self._device)
        self._spi.max_speed_hz = self._hz
        self._spi.mode = 0
        self._spi.bits_per_word = 8
        logger.info("SCL3300 on SPI{}.{} at {} Hz", self._bus, self._device, self._hz)

    def close(self) -> None:
        if self._spi is not None:
            self._spi.close()
            self._spi = None
            logger.info("SCL3300 closed")

    def __enter__(self) -> "SCL3300":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # --- frames ------------------------------------------------------------

    def _settle(self) -> None:
        if self._inter_frame_s <= 0:
            return
        end = time.perf_counter() + self._inter_frame_s
        while time.perf_counter() < end:
            pass

    def _frame(self, word: int) -> int:
        """Clock out one 32-bit word, return the 32 bits clocked in alongside it."""
        if self._spi is None:
            raise SCL3300Error("SPI is not open")
        tx = [(word >> 24) & 0xFF, (word >> 16) & 0xFF, (word >> 8) & 0xFF, word & 0xFF]
        rx = self._spi.xfer2(tx)
        self._settle()
        return (rx[0] << 24) | (rx[1] << 16) | (rx[2] << 8) | rx[3]

    @staticmethod
    def _decode(raw: int) -> Response:
        if crc8(raw >> 8) != (raw & 0xFF):
            raise SCL3300Error("CRC mismatch on 0x{:08X}".format(raw))
        return Response(addr=(raw >> 26) & 0x1F, rs=(raw >> 24) & 0x03,
                        data=(raw >> 8) & 0xFFFF)

    def _exchange(self, words: Iterable[int]) -> list[Response]:
        """Run a batch of requests and return one response per request.

        The trailing status read exists only to carry the last real answer out;
        its own answer is left pending and gets discarded as the first response
        of whatever call comes next. That is why the first response here is
        dropped rather than checked.
        """
        batch = list(words)
        raw = [self._frame(w) for w in batch + [request(REG_STATUS)]]
        out = [self._decode(r) for r in raw[1:]]
        for req, resp in zip(batch, out):
            want = (req >> 26) & 0x1F
            if resp.addr != want:
                # Not fatal on its own -- log it, because on a shared bus a
                # slipped frame is the first thing worth seeing.
                logger.warning("SCL3300 answered addr 0x{:02X}, asked 0x{:02X}",
                               resp.addr, want)
        return out

    # --- registers ---------------------------------------------------------

    def read_register(self, addr: int) -> Response:
        return self._exchange([request(addr)])[0]

    def write_register(self, addr: int, value: int) -> Response:
        return self._exchange([request(addr, value, write=True)])[0]

    # --- lifecycle ---------------------------------------------------------

    def start(self, settle_s: float = 0.1) -> None:
        """Reset, select the mode, enable the angle outputs, clear the flags.

        The default settle is deliberately longer than the datasheet minimum:
        this runs once, and a short wait here shows up as a bogus first sample.
        """
        self.write_register(REG_CMD, _CMD_SW_RESET)
        time.sleep(0.003)
        self.write_register(REG_CMD, self._mode)
        self.write_register(REG_ANG_CTRL, _ANG_CTRL_ENABLE)
        time.sleep(settle_s)

        # The status register latches faults; reading it clears them. The first
        # read still carries the startup flags, so it is the second that means
        # anything.
        self.read_register(REG_STATUS)
        status = self.read_register(REG_STATUS)
        if status.rs != RS_NORMAL:
            raise SCL3300Error(
                "device not ready after start: RS={} ({}), STATUS=0x{:04X}".format(
                    status.rs, rs_text(status.rs), status.data))

        who = self.whoami()
        if who != WHOAMI_EXPECTED:
            raise SCL3300Error("WHOAMI is 0x{:02X}, expected 0x{:02X}".format(
                who, WHOAMI_EXPECTED))
        logger.info("SCL3300 ready (mode {}, WHOAMI 0x{:02X})", self._mode + 1, who)

    def wake(self) -> None:
        self.write_register(REG_CMD, self._mode)

    def power_down(self) -> None:
        self.write_register(REG_CMD, _CMD_POWER_DOWN)

    # --- measurements ------------------------------------------------------

    def read(self) -> Reading:
        """One pipelined pass: three angles, three accelerations, temperature.

        Nine frames in a single batch rather than seven separate reads -- on a
        bus shared with the panel, fewer round trips means fewer chances to be
        interleaved mid-sample.
        """
        ang_x, ang_y, ang_z, acc_x, acc_y, acc_z, temp, status = self._exchange([
            request(REG_ANG_X), request(REG_ANG_Y), request(REG_ANG_Z),
            request(REG_ACC_X), request(REG_ACC_Y), request(REG_ACC_Z),
            request(REG_TEMP), request(REG_STATUS),
        ])
        scale = _ACC_SENSITIVITY[self._mode]
        return Reading(
            angle_x=to_degrees(ang_x.signed),
            angle_y=to_degrees(ang_y.signed),
            angle_z=to_degrees(ang_z.signed),
            acc_x=acc_x.signed / scale,
            acc_y=acc_y.signed / scale,
            acc_z=acc_z.signed / scale,
            temperature=to_celsius(temp.data),
            rs=status.rs,
        )

    def read_angles(self) -> tuple[float, float, float]:
        x, y, z = self._exchange([request(REG_ANG_X), request(REG_ANG_Y),
                                  request(REG_ANG_Z)])
        return to_degrees(x.signed), to_degrees(y.signed), to_degrees(z.signed)

    def read_temperature(self) -> float:
        return to_celsius(self.read_register(REG_TEMP).data)

    # --- diagnostics -------------------------------------------------------

    def status(self) -> dict[str, int]:
        """Raw fault registers. Decode the bits against the datasheet tables."""
        st, e1, e2 = self._exchange([
            request(REG_STATUS), request(REG_ERR_FLAG1), request(REG_ERR_FLAG2),
        ])
        return {"status": st.data, "err_flag1": e1.data, "err_flag2": e2.data,
                "rs": st.rs}

    def whoami(self) -> int:
        return self.read_register(REG_WHOAMI).data & 0xFF

    def serial_number(self) -> str:
        # SERIAL1/SERIAL2 live in bank 1; everything else this driver touches is
        # in bank 0, so switch back before returning.
        self.write_register(REG_SELBANK, 1)
        s1, s2 = self._exchange([request(REG_SERIAL1), request(REG_SERIAL2)])
        self.write_register(REG_SELBANK, 0)
        return "{:05d}{:05d}".format(s2.data, s1.data)
