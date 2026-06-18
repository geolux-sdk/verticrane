# coding:UTF-8
import threading
import time
import serial
from serial import SerialException


# Serial port configuration
class SerialConfig:
    # Serial port name
    portName = ''

    # Baud rate
    baud = 9600


# Device instance
class DeviceModel:
    # region Attributes

    # Device name
    deviceName = "My Device"

    # Device Modbus ID
    ADDR = 0x50

    # Device data dictionary
    deviceData = {}

    # Whether the device is open
    isOpen = False

    # Whether to loop-read data
    loop = False

    # Serial port
    serialPort = None

    # Serial receive thread
    readThread = None

    # Serial port configuration
    serialConfig = SerialConfig()

    # Temporary byte buffer
    TempBytes = []

    # Start register
    statReg = None

    # endregion

    # region CRC calculation
    auchCRCHi = [
        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81,
        0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0,
        0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01,
        0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41,
        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81,
        0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0,
        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01,
        0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40,
        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81,
        0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0,
        0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01,
        0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41,
        0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81,
        0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0,
        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01,
        0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41,
        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81,
        0x40]

    auchCRCLo = [
        0x00, 0xC0, 0xC1, 0x01, 0xC3, 0x03, 0x02, 0xC2, 0xC6, 0x06, 0x07, 0xC7, 0x05, 0xC5, 0xC4,
        0x04, 0xCC, 0x0C, 0x0D, 0xCD, 0x0F, 0xCF, 0xCE, 0x0E, 0x0A, 0xCA, 0xCB, 0x0B, 0xC9, 0x09,
        0x08, 0xC8, 0xD8, 0x18, 0x19, 0xD9, 0x1B, 0xDB, 0xDA, 0x1A, 0x1E, 0xDE, 0xDF, 0x1F, 0xDD,
        0x1D, 0x1C, 0xDC, 0x14, 0xD4, 0xD5, 0x15, 0xD7, 0x17, 0x16, 0xD6, 0xD2, 0x12, 0x13, 0xD3,
        0x11, 0xD1, 0xD0, 0x10, 0xF0, 0x30, 0x31, 0xF1, 0x33, 0xF3, 0xF2, 0x32, 0x36, 0xF6, 0xF7,
        0x37, 0xF5, 0x35, 0x34, 0xF4, 0x3C, 0xFC, 0xFD, 0x3D, 0xFF, 0x3F, 0x3E, 0xFE, 0xFA, 0x3A,
        0x3B, 0xFB, 0x39, 0xF9, 0xF8, 0x38, 0x28, 0xE8, 0xE9, 0x29, 0xEB, 0x2B, 0x2A, 0xEA, 0xEE,
        0x2E, 0x2F, 0xEF, 0x2D, 0xED, 0xEC, 0x2C, 0xE4, 0x24, 0x25, 0xE5, 0x27, 0xE7, 0xE6, 0x26,
        0x22, 0xE2, 0xE3, 0x23, 0xE1, 0x21, 0x20, 0xE0, 0xA0, 0x60, 0x61, 0xA1, 0x63, 0xA3, 0xA2,
        0x62, 0x66, 0xA6, 0xA7, 0x67, 0xA5, 0x65, 0x64, 0xA4, 0x6C, 0xAC, 0xAD, 0x6D, 0xAF, 0x6F,
        0x6E, 0xAE, 0xAA, 0x6A, 0x6B, 0xAB, 0x69, 0xA9, 0xA8, 0x68, 0x78, 0xB8, 0xB9, 0x79, 0xBB,
        0x7B, 0x7A, 0xBA, 0xBE, 0x7E, 0x7F, 0xBF, 0x7D, 0xBD, 0xBC, 0x7C, 0xB4, 0x74, 0x75, 0xB5,
        0x77, 0xB7, 0xB6, 0x76, 0x72, 0xB2, 0xB3, 0x73, 0xB1, 0x71, 0x70, 0xB0, 0x50, 0x90, 0x91,
        0x51, 0x93, 0x53, 0x52, 0x92, 0x96, 0x56, 0x57, 0x97, 0x55, 0x95, 0x94, 0x54, 0x9C, 0x5C,
        0x5D, 0x9D, 0x5F, 0x9F, 0x9E, 0x5E, 0x5A, 0x9A, 0x9B, 0x5B, 0x99, 0x59, 0x58, 0x98, 0x88,
        0x48, 0x49, 0x89, 0x4B, 0x8B, 0x8A, 0x4A, 0x4E, 0x8E, 0x8F, 0x4F, 0x8D, 0x4D, 0x4C, 0x8C,
        0x44, 0x84, 0x85, 0x45, 0x87, 0x47, 0x46, 0x86, 0x82, 0x42, 0x43, 0x83, 0x41, 0x81, 0x80,
        0x40]

    # endregion CRC calculation

    def __init__(self, deviceName, portName, baud, ADDR, callback_method):
        print("Initializing device model")
        # Custom device name
        self.deviceName = deviceName
        # Serial port name
        self.serialConfig.portName = portName
        # Serial baud rate
        self.serialConfig.baud = baud
        # Modbus device address
        self.ADDR = ADDR
        self.deviceData = {}
        self.callback_method = callback_method

    # Calculate Modbus CRC.
    def get_crc(self, datas, dlen):
        tempH = 0xff  # Initialize high CRC byte.
        tempL = 0xff  # Initialize low CRC byte.
        for i in range(0, dlen):
            tempIndex = (tempH ^ datas[i]) & 0xff
            tempH = (tempL ^ self.auchCRCHi[tempIndex]) & 0xff
            tempL = self.auchCRCLo[tempIndex]
        return (tempH << 8) | tempL
        pass

    # region Device data access

    # Set device data.
    def set(self, key, value):
        # Store data by key.
        self.deviceData[key] = value

    # Get device data.
    def get(self, key):
        # Return None if the key does not exist.
        if key in self.deviceData:
            return self.deviceData[key]
        else:
            return None

    # Delete device data.
    def remove(self, key):
        # Delete the stored value for this key.
        del self.deviceData[key]

    # endregion

    # Open the device.
    def openDevice(self):
        # Close any existing connection first.
        self.closeDevice()
        try:
            self.serialPort = serial.Serial(self.serialConfig.portName, self.serialConfig.baud, timeout=0.5)
            self.isOpen = True
            print("{} opened".format(self.serialConfig.portName))
            # Start a thread to continuously listen for serial port data.
            self.readThread = threading.Thread(target=self.readDataTh, args=("Data-Received-Thread", 10,))
            self.readThread.start()
            print("Device opened successfully")
        except SerialException:
            print("Failed to open " + self.serialConfig.portName)

    # Listen for serial data in a worker thread.
    def readDataTh(self, threadName, delay):
        print("Starting " + threadName)
        while True:
            # Read only while the serial port is open.
            if self.isOpen:
                try:
                    tLen = self.serialPort.inWaiting()
                    if tLen > 0:
                        data = self.serialPort.read(tLen)
                        print("RX: {}".format(data.hex(" ").upper()))
                        self.onDataReceived(data)
                except Exception as ex:
                    print(ex)
            else:
                time.sleep(0.1)
                print("Serial port is not open")
                break

    # Close the device.
    def closeDevice(self):
        self.isOpen = False
        if self.readThread is not None and self.readThread.is_alive() and self.readThread is not threading.current_thread():
            self.readThread.join(timeout=1.0)
        self.readThread = None
        if self.serialPort is not None:
            self.serialPort.close()
            self.serialPort = None
            print("Port closed")
        print("Device closed")

    # region Data parsing

    # Process received serial port data.
    def onDataReceived(self, data):
        tempdata = bytes.fromhex(data.hex())
        for val in tempdata:
            self.TempBytes.append(val)
            # Check the device ID.
            if self.TempBytes[0] != self.ADDR:
                del self.TempBytes[0]
                continue
            # Check whether function code 0x03 is used for reading.
            if len(self.TempBytes) > 2:
                if not (self.TempBytes[1] == 0x03):
                    del self.TempBytes[0]
                    continue
                tLen = len(self.TempBytes)
                # Wait until a complete protocol packet is buffered.
                if tLen == self.TempBytes[2] + 5:
                    # Verify CRC.
                    tempCrc = self.get_crc(self.TempBytes, tLen - 2)
                    if (tempCrc >> 8) == self.TempBytes[tLen - 2] and (tempCrc & 0xff) == self.TempBytes[tLen - 1]:
                        self.processData(self.TempBytes[2])
                    else:
                        del self.TempBytes[0]

    # Parse buffered data.
    def processData(self, length):
        # Parse standard sensor data packets.
        if length == 30:
            AccX = self.getSignInt16(self.TempBytes[3] << 8 | self.TempBytes[4]) / 32768 * 16
            AccY = self.getSignInt16(self.TempBytes[5] << 8 | self.TempBytes[6]) / 32768 * 16
            AccZ = self.getSignInt16(self.TempBytes[7] << 8 | self.TempBytes[8]) / 32768 * 16
            self.set("AccX", round(AccX, 3))
            self.set("AccY", round(AccY, 3))
            self.set("AccZ", round(AccZ, 3))

            AsX = self.getSignInt16(self.TempBytes[9] << 8 | self.TempBytes[10]) / 32768 * 2000
            AsY = self.getSignInt16(self.TempBytes[11] << 8 | self.TempBytes[12]) / 32768 * 2000
            AsZ = self.getSignInt16(self.TempBytes[13] << 8 | self.TempBytes[14]) / 32768 * 2000
            self.set("AsX", round(AsX, 3))
            self.set("AsY", round(AsY, 3))
            self.set("AsZ", round(AsZ, 3))

            HX = self.getSignInt16(self.TempBytes[15] << 8 | self.TempBytes[16]) * 13 / 1000
            HY = self.getSignInt16(self.TempBytes[17] << 8 | self.TempBytes[18]) * 13 / 1000
            HZ = self.getSignInt16(self.TempBytes[19] << 8 | self.TempBytes[20]) * 13 / 1000
            self.set("HX", round(HX, 3))
            self.set("HY", round(HY, 3))
            self.set("HZ", round(HZ, 3))

            AngX = self.getSignInt32(
                self.TempBytes[23] << 24 | self.TempBytes[24] << 16 | self.TempBytes[21] << 8 | self.TempBytes[
                    22]) / 1000
            AngY = self.getSignInt32(
                self.TempBytes[27] << 24 | self.TempBytes[28] << 16 | self.TempBytes[25] << 8 | self.TempBytes[
                    26]) / 1000
            AngZ = self.getSignInt32(
                self.TempBytes[31] << 24 | self.TempBytes[32] << 16 | self.TempBytes[29] << 8 | self.TempBytes[
                    30]) / 1000
            self.set("AngX", round(AngX, 3))
            self.set("AngY", round(AngY, 3))
            self.set("AngZ", round(AngZ, 3))
            self.callback_method(self)
        else:
            self.processRegisterData(length)
        self.TempBytes.clear()

    # endregion

    def processRegisterData(self, length):
        if self.statReg is None:
            return

        values = []
        for i in range(int(length / 2)):
            values.append(self.getSignInt16(self.TempBytes[2 * i + 3] << 8 | self.TempBytes[2 * i + 4]))

        if self.statReg == 0x43 and len(values) >= 1:
            self.set("Temp", round(values[0] / 100, 2))
            self.callback_method(self)
            return

        if self.statReg == 0x51 and len(values) >= 4:
            self.set("q0", round(values[0] / 32768, 6))
            self.set("q1", round(values[1] / 32768, 6))
            self.set("q2", round(values[2] / 32768, 6))
            self.set("q3", round(values[3] / 32768, 6))
            self.callback_method(self)
            return

        for value in values:
            value = value / 32768
            self.set(str(self.statReg), round(value, 3))
            self.statReg += 1

    @staticmethod
    def getSignInt16(num):
        if num >= pow(2, 15):
            num -= pow(2, 16)
        return num

    @staticmethod
    def getSignInt32(num):
        if num >= pow(2, 31):
            num -= pow(2, 32)
        return num

    @staticmethod
    def formatBytes(data):
        return " ".join("{:02X}".format(val & 0xff) for val in data)

    def logModbusTx(self, data):
        if len(data) < 6:
            print("MODBUS TX: frame too short")
            return

        slave = data[0]
        function = data[1]
        register = (data[2] << 8) | data[3]
        value = (data[4] << 8) | data[5]

        if function == 0x03:
            print(
                "MODBUS READ: slave=0x{0:02X}, func=0x03, start=0x{1:04X}, count={2}".format(
                    slave, register, value
                )
            )
        elif function == 0x06:
            print(
                "MODBUS WRITE: slave=0x{0:02X}, func=0x06, register=0x{1:04X}, value=0x{2:04X}".format(
                    slave, register, value
                )
            )
        else:
            print("MODBUS TX: slave=0x{0:02X}, func=0x{1:02X}".format(slave, function))

    # Send serial port data.
    def sendData(self, data):
        try:
            self.logModbusTx(data)
            print("TX: {}".format(self.formatBytes(data)))
            self.serialPort.write(data)
        except Exception as ex:
            print(ex)

    # Read registers.
    def readReg(self, regAddr, regCount):
        # Store the start register for parsing returned data.
        self.statReg = regAddr
        # Build and send the read command.
        self.sendData(self.get_readBytes(self.ADDR, regAddr, regCount))

    # Write a register.
    def writeReg(self, regAddr, sValue):
        # Unlock before writing.
        self.unlock()
        # Delay 100 ms.
        time.sleep(0.1)
        # Build and send the write command.
        self.sendData(self.get_writeBytes(self.ADDR, regAddr, sValue))
        # Delay 100 ms.
        time.sleep(0.1)
        # Save the device settings.
        self.save()

    # Build a read command.
    def get_readBytes(self, devid, regAddr, regCount):
        # Initialize the command buffer.
        tempBytes = [None] * 8
        # Device Modbus address.
        tempBytes[0] = devid
        # Read function code.
        tempBytes[1] = 0x03
        # Register high byte.
        tempBytes[2] = regAddr >> 8
        # Register low byte.
        tempBytes[3] = regAddr & 0xff
        # Register count high byte.
        tempBytes[4] = regCount >> 8
        # Register count low byte.
        tempBytes[5] = regCount & 0xff
        # Calculate CRC.
        tempCrc = self.get_crc(tempBytes, len(tempBytes) - 2)
        # CRC high byte.
        tempBytes[6] = tempCrc >> 8
        # CRC low byte.
        tempBytes[7] = tempCrc & 0xff
        return tempBytes

    # Build a write command.
    def get_writeBytes(self, devid, regAddr, sValue):
        # Initialize the command buffer.
        tempBytes = [None] * 8
        # Device Modbus address.
        tempBytes[0] = devid
        # Write function code.
        tempBytes[1] = 0x06
        # Register high byte.
        tempBytes[2] = regAddr >> 8
        # Register low byte.
        tempBytes[3] = regAddr & 0xff
        # Register value high byte.
        tempBytes[4] = sValue >> 8
        # Register value low byte.
        tempBytes[5] = sValue & 0xff
        # Calculate CRC.
        tempCrc = self.get_crc(tempBytes, len(tempBytes) - 2)
        # CRC high byte.
        tempBytes[6] = tempCrc >> 8
        # CRC low byte.
        tempBytes[7] = tempCrc & 0xff
        return tempBytes

    # Start loop reading.
    def startLoopRead(self):
        # Enable loop reading.
        self.loop = True
        # Start the read thread.
        t = threading.Thread(target=self.loopRead, args=())
        t.start()

    # Loop-read data.
    def loopRead(self):
        print("Loop reading started")
        while self.loop:
            self.readReg(0x34, 15)
            time.sleep(0.05)
            self.readReg(0x43, 1)
            time.sleep(0.05)
            self.readReg(0x51, 4)
            time.sleep(0.2)
        print("Loop reading stopped")

    # Stop loop reading.
    def stopLoopRead(self):
        self.loop = False

    # Unlock the device.
    def unlock(self):
        cmd = self.get_writeBytes(self.ADDR, 0x69, 0xb588)
        self.sendData(cmd)

    # Save device settings.
    def save(self):
        cmd = self.get_writeBytes(self.ADDR, 0x00, 0x0000)
        self.sendData(cmd)
