import device_model
import time


# Called when device data is updated.
def updateData(DeviceModel):
    print(DeviceModel.deviceData)


if __name__ == "__main__":
    # Create the device model.
    device = device_model.DeviceModel("Test Device", "COM51", 115200, 0x50, updateData)
    # Open the device.
    device.openDevice()
    # Enable loop reading.
    device.startLoopRead()

    # Read one register from 0x3a.
    # device.readReg(0x3a, 1)
    # Get the read result.
    # device.get(str(0x3a))

    # Write value 50 to register 0x65.
    # device.writeReg(0x65, 50)
