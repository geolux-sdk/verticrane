import device_model
import time


# Called when device data is updated.
def updateData(DeviceModel):
    data = DeviceModel.deviceData
    print(
        "Acc: X={AccX}, Y={AccY}, Z={AccZ} | "
        "Gyro: X={AsX}, Y={AsY}, Z={AsZ} | "
        "Mag: X={HX}, Y={HY}, Z={HZ} | "
        "Angle: X={AngX}, Y={AngY}, Z={AngZ}".format(**data)
    )


if __name__ == "__main__":
    # Create the device model.
    device = device_model.DeviceModel("Test Device", "COM11", 9600, 0x50, updateData)
    # Open the device.
    device.openDevice()
    try:
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

    # Read one register from 0x3a.
    # device.readReg(0x3a, 1)
    # Get the read result.
    # device.get(str(0x3a))

    # Write value 50 to register 0x65.
    # device.writeReg(0x65, 50)
