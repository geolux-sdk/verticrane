# coding:UTF-8
# Minimal driver for the Good Display GDEY0154D67 e-paper panel (1.54", 200x200,
# SSD1681 controller) on the Raspberry Pi's SPI0 bus.
#
# Written by hand rather than pulling in a vendor library: the panel needs only a
# handful of SSD1681 commands, and the common vendor drivers depend on RPi.GPIO,
# which is unreliable on recent kernels. spidev + gpiozero keeps the dependency
# footprint small, which matters on the 415 MB Pi Zero 2 W.
#
# Wiring (40-pin header):
#   DIN  -> GPIO10 / MOSI (pin 19)      RST  -> GPIO17 (pin 11)
#   CLK  -> GPIO11 / SCLK (pin 23)      BUSY -> GPIO24 (pin 18)
#   CS   -> GPIO8  / CE0  (pin 24)      DC   -> GPIO25 (pin 22)
#   VCC  -> 3.3V (pin 1)                GND  -> GND    (pin 6)
#
# The sensor UART lives on GPIO14/15 (pins 8/10) and does not overlap.

from __future__ import annotations

import time
from typing import Optional

from loguru import logger

WIDTH = 200
HEIGHT = 200
_ROW_BYTES = WIDTH // 8          # 25 bytes per row; 200 is a whole number of bytes
_FRAME_BYTES = _ROW_BYTES * HEIGHT

# SSD1681 commands used here.
_CMD_DRIVER_OUTPUT = 0x01
_CMD_DEEP_SLEEP = 0x10
_CMD_DATA_ENTRY = 0x11
_CMD_SW_RESET = 0x12
_CMD_TEMP_SENSOR = 0x18
_CMD_MASTER_ACTIVATE = 0x20
_CMD_UPDATE_CTRL1 = 0x21
_CMD_UPDATE_CTRL2 = 0x22
_CMD_WRITE_RAM = 0x24
_CMD_WRITE_RAM_PREV = 0x26
_CMD_BORDER = 0x3C
_CMD_ANALOG_BLOCK = 0x74
_CMD_DIGITAL_BLOCK = 0x7E
_CMD_RAM_X_RANGE = 0x44
_CMD_RAM_Y_RANGE = 0x45
_CMD_RAM_X_COUNT = 0x4E
_CMD_RAM_Y_COUNT = 0x4F

# Update sequences for command 0x22. Full clears ghosting; fast reuses the panel's
# stored LUT and is roughly 4x quicker, at the cost of some residual contrast.
UPDATE_FULL = 0xC7
UPDATE_FAST = 0xC7
# Enable clock and analog, load the temperature reading and the OTP waveform, then
# power the analog section back down. Run once during init, before any refresh.
LOAD_TEMP_AND_LUT = 0xB1


class GDEY0154D67:
    def __init__(self, dc: int = 25, rst: int = 17, busy: int = 24,
                 spi_bus: int = 0, spi_device: int = 0, spi_hz: int = 4000000) -> None:
        self._dc_pin, self._rst_pin, self._busy_pin = dc, rst, busy
        self._bus, self._device, self._hz = spi_bus, spi_device, spi_hz
        self._spi = None
        self._dc = None
        self._rst = None
        self._busy = None

    # --- low level ---------------------------------------------------------

    def open(self) -> None:
        # Imported here so the module can be inspected on a machine without the
        # Pi-only packages installed.
        import spidev
        from gpiozero import DigitalInputDevice, DigitalOutputDevice

        self._spi = spidev.SpiDev()
        self._spi.open(self._bus, self._device)
        self._spi.max_speed_hz = self._hz
        self._spi.mode = 0
        self._dc = DigitalOutputDevice(self._dc_pin, initial_value=False)
        self._rst = DigitalOutputDevice(self._rst_pin, initial_value=True)
        self._busy = DigitalInputDevice(self._busy_pin)
        logger.info("SPI{}.{} opened at {} Hz (DC={}, RST={}, BUSY={})",
                    self._bus, self._device, self._hz,
                    self._dc_pin, self._rst_pin, self._busy_pin)

    def close(self) -> None:
        for dev in (self._dc, self._rst, self._busy):
            if dev is not None:
                dev.close()
        if self._spi is not None:
            self._spi.close()
        self._spi = self._dc = self._rst = self._busy = None
        logger.info("Panel closed")

    def _command(self, value: int, *data: int) -> None:
        self._dc.off()
        self._spi.writebytes([value])
        if data:
            self._write_data(list(data))

    def _write_data(self, payload: list[int] | bytes) -> None:
        self._dc.on()
        # spidev caps a single transfer; chunk so a full 5000-byte frame goes out.
        chunk = 4096
        buf = list(payload)
        for i in range(0, len(buf), chunk):
            self._spi.writebytes(buf[i:i + chunk])

    def _wait_while_busy(self, timeout_s: float = 10.0) -> None:
        # BUSY is driven high while the panel is working. The controller needs a moment
        # to assert it, so sample after a short settle rather than racing the command.
        time.sleep(0.005)
        started = time.monotonic()
        deadline = started + timeout_s
        while self._busy.value:
            if time.monotonic() > deadline:
                raise TimeoutError("Panel BUSY stayed high for {0:.0f}s".format(timeout_s))
            time.sleep(0.01)
        logger.debug("BUSY cleared after {:.0f} ms", (time.monotonic() - started) * 1000)

    # --- panel -------------------------------------------------------------

    def reset(self) -> None:
        self._rst.on()
        time.sleep(0.02)
        self._rst.off()
        time.sleep(0.010)
        self._rst.on()
        time.sleep(0.020)

    def init(self) -> None:
        self.reset()
        self._wait_while_busy()
        self._command(_CMD_SW_RESET)
        self._wait_while_busy()

        # Configure the controller's analog and digital blocks. These are absent from
        # the register tables but present in every SSD1681 vendor sample; without them
        # the charge pump stays down, so the refresh timing looks perfect while the
        # panel is never actually driven.
        self._command(_CMD_ANALOG_BLOCK, 0x54)
        self._command(_CMD_DIGITAL_BLOCK, 0x3B)

        # This is the sequence Waveshare ships for their 1.54" V2 module, which uses the
        # same SSD1681 at the same 200x200 geometry. Deviating from it cost us an
        # afternoon: an added 0x21 with 0x80 restricts the available sources to
        # S8..S167, which is wrong for a 200-wide panel.
        self._command(_CMD_DRIVER_OUTPUT, HEIGHT - 1, 0x00, 0x00)
        self._command(_CMD_DATA_ENTRY, 0x01)                  # X increments, Y decrements
        self._command(_CMD_RAM_X_RANGE, 0x00, _ROW_BYTES - 1)
        self._command(_CMD_RAM_Y_RANGE, HEIGHT - 1, 0x00, 0x00, 0x00)
        self._command(_CMD_BORDER, 0x01)
        self._command(_CMD_TEMP_SENSOR, 0x80)                 # use the internal sensor

        # Load the waveform from OTP for the measured temperature. Without this the
        # controller has no LUT, accepts a refresh command and then holds BUSY high
        # forever instead of driving the panel.
        self._command(_CMD_UPDATE_CTRL2, LOAD_TEMP_AND_LUT)
        self._command(_CMD_MASTER_ACTIVATE)

        self._set_cursor()
        self._wait_while_busy()
        logger.info("Panel initialised ({}x{})", WIDTH, HEIGHT)

    def _set_cursor(self) -> None:
        self._command(_CMD_RAM_X_COUNT, 0x00)
        self._command(_CMD_RAM_Y_COUNT, HEIGHT - 1, 0x00)

    def display(self, frame: bytes, mode: int = UPDATE_FULL) -> None:
        if len(frame) != _FRAME_BYTES:
            raise ValueError("Frame must be {0} bytes, got {1}".format(_FRAME_BYTES, len(frame)))
        self._set_cursor()
        self._command(_CMD_WRITE_RAM)
        self._write_data(frame)
        # RAM2 holds the "previous" image the controller differences against. Its
        # power-on contents are undefined, so seed it with the same frame -- otherwise
        # a differential waveform can compute no change and the panel never moves.
        self._set_cursor()
        self._command(_CMD_WRITE_RAM_PREV)
        self._write_data(frame)
        self._command(_CMD_UPDATE_CTRL2, mode)
        self._command(_CMD_MASTER_ACTIVATE)
        self._wait_while_busy()

    def clear(self, white: bool = True) -> None:
        # In panel RAM a set bit is white, a clear bit is black.
        self.display(bytes([0xFF if white else 0x00]) * _FRAME_BYTES)

    def sleep(self) -> None:
        # Deep sleep holds the image and cuts standby draw; init() is needed to wake.
        self._command(_CMD_DEEP_SLEEP, 0x01)
        time.sleep(0.1)
        logger.info("Panel in deep sleep")


def image_to_frame(image, mirror_x: bool = True) -> bytes:
    # PIL packs mode "1" images MSB-first with 1 = white, which is exactly the
    # panel's RAM convention, so no inversion is needed.
    #
    # The panel's source order runs opposite to PIL's, so the image arrives mirrored
    # left-to-right unless we flip it here. This has to happen on the image rather
    # than through the controller's X address direction: RAM X addresses step a byte
    # at a time, so reversing them would swap 8-pixel blocks while leaving the bits
    # inside each block in their original order.
    from PIL import Image as _Image

    if image.size != (WIDTH, HEIGHT):
        raise ValueError("Image must be {0}x{1}, got {2}x{3}".format(
            WIDTH, HEIGHT, image.size[0], image.size[1]))
    image = image.convert("1")
    if mirror_x:
        image = image.transpose(_Image.FLIP_LEFT_RIGHT)
    return image.tobytes()
