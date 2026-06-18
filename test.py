import device_model
import time


# Called when device data is updated.
def updateData(DeviceModel):
    data = DeviceModel.deviceData
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
