# coding:UTF-8
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from loguru import logger
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException


# Serial port configuration
class SerialConfig:
    # Serial port name
    portName: str = ''

    # Baud rate
    baud: int = 9600


# Device instance
class HWT9037_485:
    # region Attributes

    # Device name
    deviceName: str = "HWT9037-485"

    # Device Modbus ID
    ADDR: int = 0x50

    # Device data dictionary (parsed, scaled values)
    deviceData: dict[str, float] = {}

    # Raw register data dictionary (unsigned 16-bit values keyed by register address)
    registerData: dict[int, int] = {}

    # Whether the device is open
    isOpen: bool = False

    # Whether to loop-read data
    loop: bool = False

    # pymodbus serial client
    client: Optional[ModbusSerialClient] = None

    # Serial port configuration
    serialConfig: SerialConfig = SerialConfig()

    # Loop-read thread
    loopThread: Optional[threading.Thread] = None

    # endregion

    def __init__(self, portName: str, baud: int, ADDR: int,
                 callback_method: Callable[["HWT9037_485"], None]) -> None:
        logger.info("Initializing device model")
        # Serial port name
        self.serialConfig.portName = portName
        # Serial baud rate
        self.serialConfig.baud = baud
        # Modbus device address
        self.ADDR = ADDR
        self.deviceData = {}
        self.registerData = {}
        self.callback_method = callback_method
        # Guards Modbus transactions against concurrent access.
        self._lock = threading.Lock()
        # Log per-transaction Modbus traffic (set False for high-rate logging).
        self.verbose = True

    # region Device data access

    # Set device data.
    def set(self, key: str, value: float) -> None:
        # Store data by key.
        self.deviceData[key] = value

    # Get device data.
    def get(self, key: str) -> Optional[float]:
        # Return None if the key does not exist.
        if key in self.deviceData:
            return self.deviceData[key]
        else:
            return None

    # Delete device data.
    def remove(self, key: str) -> None:
        # Delete the stored value for this key.
        del self.deviceData[key]

    # endregion

    # Open the device.
    def openDevice(self) -> None:
        # pymodbus builds RTU frames and CRC automatically.
        self.client = ModbusSerialClient(
            port=self.serialConfig.portName,
            baudrate=self.serialConfig.baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.5,
        )
        if self.client.connect():
            self.isOpen = True
            logger.info("{} opened", self.serialConfig.portName)
            logger.info("Device opened successfully")
        else:
            self.isOpen = False
            logger.error("Failed to open {}", self.serialConfig.portName)

    def setBaudrate(self, baud: int) -> None:
        self.serialConfig.baud = baud

    # Close the device.
    def closeDevice(self) -> None:
        self.stopLoopRead()
        if self.loopThread is not None and self.loopThread.is_alive() \
                and self.loopThread is not threading.current_thread():
            self.loopThread.join(timeout=1.0)
        self.loopThread = None
        self.isOpen = False
        if self.client is not None:
            self.client.close()
            self.client = None
            logger.info("Port closed")
        logger.info("Device closed")

    # region Data parsing

    # Store raw registers and parse known data blocks.
    def parseRegisters(self, regAddr: int, regs: list[int]) -> None:
        # Always keep the raw unsigned values, keyed by register address.
        for i, raw in enumerate(regs):
            self.registerData[regAddr + i] = raw & 0xFFFF

        # Standard 9-axis + angle block (0x34, 15 registers).
        if regAddr == 0x34 and len(regs) >= 15:
            self.set("AccX", round(self.getSignInt16(regs[0]) / 32768 * 16, 3))
            self.set("AccY", round(self.getSignInt16(regs[1]) / 32768 * 16, 3))
            self.set("AccZ", round(self.getSignInt16(regs[2]) / 32768 * 16, 3))

            self.set("AsX", round(self.getSignInt16(regs[3]) / 32768 * 2000, 3))
            self.set("AsY", round(self.getSignInt16(regs[4]) / 32768 * 2000, 3))
            self.set("AsZ", round(self.getSignInt16(regs[5]) / 32768 * 2000, 3))

            self.set("HX", round(self.getSignInt16(regs[6]) * 13 / 1000, 3))
            self.set("HY", round(self.getSignInt16(regs[7]) * 13 / 1000, 3))
            self.set("HZ", round(self.getSignInt16(regs[8]) * 13 / 1000, 3))

            # Each angle is a 32-bit value: low word first, high word next.
            AngX: float = self.getSignInt32((regs[10] << 16) | regs[9]) / 1000
            AngY: float = self.getSignInt32((regs[12] << 16) | regs[11]) / 1000
            AngZ: float = self.getSignInt32((regs[14] << 16) | regs[13]) / 1000
            self.set("AngX", round(AngX, 3))
            self.set("AngY", round(AngY, 3))
            self.set("AngZ", round(AngZ, 3))
            self.callback_method(self)
            return

        # Temperature (0x43, 1 register).
        if regAddr == 0x43 and len(regs) >= 1:
            self.set("Temp", round(self.getSignInt16(regs[0]) / 100, 2))
            self.callback_method(self)
            return

        # Quaternion (0x51, 4 registers).
        if regAddr == 0x51 and len(regs) >= 4:
            self.set("q0", round(self.getSignInt16(regs[0]) / 32768, 6))
            self.set("q1", round(self.getSignInt16(regs[1]) / 32768, 6))
            self.set("q2", round(self.getSignInt16(regs[2]) / 32768, 6))
            self.set("q3", round(self.getSignInt16(regs[3]) / 32768, 6))
            self.callback_method(self)
            return

    # endregion

    # Read registers (function code 0x03).
    def readReg(self, regAddr: int, regCount: int) -> Optional[list[int]]:
        if self.client is None or not self.isOpen:
            logger.warning("Device is not open")
            return None
        try:
            with self._lock:
                rr = self.client.read_holding_registers(
                    regAddr, count=regCount, device_id=self.ADDR
                )
            if rr.isError():
                logger.error("MODBUS READ error: start=0x{:04X}, count={} -> {}",
                             regAddr, regCount, rr)
                return None
            if self.verbose:
                logger.debug("MODBUS READ: slave=0x{:02X}, func=0x03, start=0x{:04X}, count={}",
                             self.ADDR, regAddr, regCount)
            self.parseRegisters(regAddr, rr.registers)
            return rr.registers
        except ModbusException as ex:
            logger.exception("MODBUS READ exception: {}", ex)
            return None

    # Write a single register (function code 0x06).
    def writeReg(self, regAddr: int, sValue: int, save: bool = False) -> None:
        # Unlock before writing.
        self.unlock()
        time.sleep(0.1)
        # Send the write command.
        self._write(regAddr, sValue)
        time.sleep(0.1)
        if save:
            # Save the device settings only when explicitly requested.
            self.save()

    # Low-level single-register write.
    def _write(self, regAddr: int, sValue: int) -> bool:
        if self.client is None or not self.isOpen:
            logger.warning("Device is not open")
            return False
        try:
            with self._lock:
                rr = self.client.write_register(
                    regAddr, sValue & 0xFFFF, device_id=self.ADDR
                )
            if rr.isError():
                logger.error("MODBUS WRITE error: register=0x{:04X}, value=0x{:04X} -> {}",
                             regAddr, sValue & 0xFFFF, rr)
                return False
            if self.verbose:
                logger.debug("MODBUS WRITE: slave=0x{:02X}, func=0x06, register=0x{:04X}, value=0x{:04X}",
                             self.ADDR, regAddr, sValue & 0xFFFF)
            return True
        except ModbusException as ex:
            logger.exception("MODBUS WRITE exception: {}", ex)
            return False

    # Start loop reading.
    def startLoopRead(self) -> None:
        # Enable loop reading.
        self.loop = True
        # Start the read thread.
        self.loopThread = threading.Thread(target=self.loopRead, args=())
        self.loopThread.start()

    # Loop-read data.
    def loopRead(self) -> None:
        logger.info("Loop reading started")
        while self.loop:
            self.readReg(0x34, 15)
            time.sleep(0.05)
            self.readReg(0x43, 1)
            time.sleep(0.05)
            self.readReg(0x51, 4)
            time.sleep(3)
        logger.info("Loop reading stopped")

    # Stop loop reading.
    def stopLoopRead(self) -> None:
        self.loop = False

    # Unlock the device.
    def unlock(self) -> None:
        self._write(0x69, 0xB588)

    # Save device settings.
    def save(self) -> None:
        self._write(0x00, 0x0000)

    # Reboot the device (SAVE register 0x00 <- 0x00FF). Reloads configuration without
    # writing flash; the device stops responding for a moment while it restarts.
    def reboot(self) -> None:
        self.unlock()
        time.sleep(0.1)
        self._write(0x00, 0x00FF)

    @staticmethod
    def getSignInt16(num: int) -> int:
        if num >= pow(2, 15):
            num -= pow(2, 16)
        return num

    @staticmethod
    def getSignInt32(num: int) -> int:
        if num >= pow(2, 31):
            num -= pow(2, 32)
        return num
