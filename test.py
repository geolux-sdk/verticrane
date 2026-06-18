from hwt9037_485 import HWT9037_485
import time


REGISTER_DUMP_RANGES = [
    (0x00, 0x2B),
    (0x2E, 0x01),
    (0x30, 0x14),
    (0x51, 0x04),
    (0x61, 0x01),
    (0x63, 0x01),
    (0x69, 0x02),
    (0x6E, 0x02),
    (0x74, 0x01),
    (0x7F, 0x06),
    (0x95, 0x04),
]


PORT_NAME = "COM11"
DEVICE_ADDR = 0x50
DEFAULT_BAUD = 9600
SESSION_BAUD = 115200
DUMP_REGISTERS_ON_START = True


# Called when device data is updated.
def updateData(device):
    data = device.deviceData
    print(
        "Acc: X={AccX}, Y={AccY}, Z={AccZ} | "
        "Gyro: X={AsX}, Y={AsY}, Z={AsZ} | "
        "Mag: X={HX}, Y={HY}, Z={HZ} | "
        "Angle: X={AngX}, Y={AngY}, Z={AngZ} | "
        "Temp: {Temp} C | "
        "Quat: q0={q0}, q1={q1}, q2={q2}, q3={q3}".format(
            AccX=data.get("AccX", "-"),
            AccY=data.get("AccY", "-"),
            AccZ=data.get("AccZ", "-"),
            AsX=data.get("AsX", "-"),
            AsY=data.get("AsY", "-"),
            AsZ=data.get("AsZ", "-"),
            HX=data.get("HX", "-"),
            HY=data.get("HY", "-"),
            HZ=data.get("HZ", "-"),
            AngX=data.get("AngX", "-"),
            AngY=data.get("AngY", "-"),
            AngZ=data.get("AngZ", "-"),
            Temp=data.get("Temp", "-"),
            q0=data.get("q0", "-"),
            q1=data.get("q1", "-"),
            q2=data.get("q2", "-"),
            q3=data.get("q3", "-"),
        )
    )


def dumpRegisters(device):
    print("Register dump started")
    for start, count in REGISTER_DUMP_RANGES:
        print("Read registers: start=0x{0:04X}, count={1}".format(start, count))
        device.readReg(start, count)
        time.sleep(0.2)
        for reg in range(start, start + count):
            value = device.registerData.get(reg)
            if value is not None:
                signed_value = value - 0x10000 if value & 0x8000 else value
                print("REG 0x{0:04X} = 0x{1:04X} ({2})".format(reg, value, signed_value))
    print("Register dump finished")


def createDevice(baud):
    return HWT9037_485(PORT_NAME, baud, DEVICE_ADDR, updateData)


def setSessionBaudrate(baud=SESSION_BAUD):
    try:
        print("Changing baudrate from 9600 to {0} bps with saving".format(baud))
        device.closeDevice()

        device.setBaudrate(baud=DEFAULT_BAUD)
        device.openDevice()
        device.readReg(0x2E, 1)
        time.sleep(0.2)
        version = device.registerData.get(0x2E)
        if version is None:
            print("{0} bps change check failed".format(SESSION_BAUD))
        else:
            print("{0} bps change check succeeded: VERSION=0x{1:04X}".format(SESSION_BAUD, version))
            device.writeReg(0x04, 0x0006, save=True)  # 0x0006 corresponds to 115200 bps
            device.closeDevice()
        device.setBaudrate(baud=baud)
        device.openDevice()

    except Exception as ex:
        print("Error changing baudrate: {}".format(ex))
        





if __name__ == "__main__":
    try:
        device = createDevice(SESSION_BAUD)
        device.openDevice()
        device.readReg(0x2E, 1)
        version = device.registerData.get(0x2E)
        if version is None:
            print("{0} bps communication check failed".format(SESSION_BAUD))
            setSessionBaudrate(baud=SESSION_BAUD)
        else:
            print("{0} bps communication OK: VERSION=0x{1:04X}".format(SESSION_BAUD, version))
        
        if DUMP_REGISTERS_ON_START:
            dumpRegisters(device)
            
        # Enable loop reading.
        device.startLoopRead()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        device.stopLoopRead()
        time.sleep(0.3)
        device.closeDevice()
        print("Stopped")
