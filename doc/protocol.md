# HWT9073 RS485 Modbus Protocol Notes

## Source Documents

- `doc/HWT9073 RS485 Manual.pdf`
  - 8-page user manual.
  - Includes RS485 wiring and links to protocol/SDK resources.
- `doc/High-precision sensor Modbus protocol.pdf`
  - 53-page Modbus protocol document.
  - Includes register table, read/write frame formats, conversion formulas, and command examples.

## RS485 Wiring

From the RS485 user manual:

- `VCC` to `5~36V`
- `B` to `B`
- `A` to `A`
- `GND` to `GND`

If the serial port opens but no `RX` log appears, check A/B wiring first.

## Serial Settings

The protocol document lists baud rate register `BAUD` at address `0x0004`.

Baud values:

- `0x01`: 4800 bps
- `0x02`: 9600 bps
- `0x03`: 19200 bps
- `0x04`: 38400 bps
- `0x05`: 57600 bps
- `0x06`: 115200 bps
- `0x07`: 230400 bps
- `0x08`: 460800 bps, model-limited
- `0x09`: 921600 bps, model-limited

Current code setting:

```python
device_model.DeviceModel("Test Device", "COM11", 9600, 0x50, updateData)
```

Meaning:

- Port: `COM11`
- Baud rate: `9600`
- Modbus slave address: `0x50`

The tested device responded at `9600 bps`. It did not respond at `115200 bps`.

## Device Address

The protocol document says the default Modbus address is `0x50`.

Device address register:

- Register: `IICADDR`
- Address: `0x001A`
- Range: `0x01` to `0x7F`
- Default: `0x0050`

Current code uses `0x50`, which matches the document default.

## Tested Initial Register Values

The following values were read from the connected device during bring-up. All rows include the Modbus register address.

### Communication And Identity

| Address | Name | Raw value | Decoded value | Note |
|---:|---|---:|---|---|
| `0x0004` | `BAUD` | `0x0002` | `9600 bps` | Confirmed working baud rate |
| `0x001A` | `IICADDR` | `0x0050` | Modbus address `0x50` | Current slave address |
| `0x002E` | `VERSION` | `0x0343` | 835 | Firmware/version code |
| `0x0074` | `MODDELAY` | `0x0BB8` | 3000 us | RS485 response delay |
| `0x007F`~`0x0084` | `NUMBERID1`~`NUMBERID6` | `0x5457 0x3234 0x3030 0x3630 0x3138 0x3135` | `TW2400601815` | Device number, ASCII interpretation |

### Operating Mode

| Address | Name | Raw value | Decoded value | Note |
|---:|---|---:|---|---|
| `0x000E` | `WORKMODE` | `0x0000` | Normal data mode | Z-axis operation mode |
| `0x001F` | `BANDWIDTH` | `0x0005` | 10 Hz | Bandwidth setting |
| `0x0020` | `GYRORANGE` | `0x0003` | 2000 deg/s | Gyroscope range |
| `0x0021` | `ACCRANGE` | `0x0003` | 16 g | Acceleration range |
| `0x0022` | `SLEEP` | `0x0000` | Sleep off | Normal operation |
| `0x0023` | `ORIENT` | `0x0000` | Horizontal installation | Installation direction |
| `0x0024` | `AXIS6` | `0x0000` | 9-axis algorithm | Magnetic field is used for heading |

### LED Register Test

| Address | Name | Raw value | Decoded value | Tested result |
|---:|---|---:|---|---|
| `0x001B` | `LEDOFF` | `0x0000` | Document says LED ON | No visible LED change |
| `0x001B` | `LEDOFF` | `0x0001` | Document says LED OFF | No visible LED change |

Conclusion: this hardware appears to have no visible LED, or the common `LEDOFF` register is not connected to a visible LED on this model.

### Calibration, Offset, And Filter Values

| Address | Name | Raw value | Signed value | Note |
|---:|---|---:|---:|---|
| `0x0005` | `AXOFFSET` | `0x0000` | 0 | Acceleration X offset |
| `0x0006` | `AYOFFSET` | `0x0000` | 0 | Acceleration Y offset |
| `0x0007` | `AZOFFSET` | `0x0000` | 0 | Acceleration Z offset |
| `0x0008` | `GXOFFSET` | `0x0000` | 0 | Gyro X offset |
| `0x0009` | `GYOFFSET` | `0x0010` | 16 | Gyro Y offset |
| `0x000A` | `GZOFFSET` | `0x0003` | 3 | Gyro Z offset |
| `0x000B` | `HXOFFSET` | `0xFF41` | -191 | Magnetic X offset |
| `0x000C` | `HYOFFSET` | `0x01B6` | 438 | Magnetic Y offset |
| `0x000D` | `HZOFFSET` | `0x0286` | 646 | Magnetic Z offset |
| `0x001C` | `MAGRANGX` | `0x01F4` | 500 | Magnetic calibration X range |
| `0x001D` | `MAGRANGY` | `0x01F4` | 500 | Magnetic calibration Y range |
| `0x001E` | `MAGRANGZ` | `0x01F4` | 500 | Magnetic calibration Z range |
| `0x0025` | `FILTK` | `0x000A` | 10 | Dynamic filter setting |
| `0x002A` | `ACCFILT` | `0x07D0` | 2000 | Acceleration filter setting |

### Gyro Stillness And Response Settings

| Address | Name | Raw value | Decoded value | Note |
|---:|---|---:|---|---|
| `0x0011` | `GPTPTIME` | `0x000A` | 10 s | Z-axis peak-to-peak acquisition time |
| `0x0012` | `GYROBAIS` | `0x000D` | 0.013 deg/s | Z-axis zero bias value |
| `0x0013` | `GBAISTIME` | `0x000A` | 10 s | Z-axis zero-bias acquisition time |
| `0x0014` | `GSTATICTHRE` | `0x0032` | 0.050 deg/s | Z-axis static threshold |
| `0x0015` | `GSTATICTIME` | `0x0064` | 0.100 s | Z-axis stabilization time |
| `0x0016` | `PGSCALE` | `0x2710` | 1.0000 | Z-axis calibration factor P |
| `0x0018` | `GSCALERANGE` | `0x02D0` | 720 deg | Z-axis calibration angle |
| `0x0061` | `GYROCALITHR` | `0x0000` | 0 deg/s | Gyro still threshold |
| `0x0063` | `GYROCALTIME` | `0x03E8` | 1000 ms | Gyro auto calibration time |
| `0x006A` | `WERROR` | `0x0000` | 0 | Gyroscope change value |
| `0x006E` | `WZTIME` | `0x01F4` | 500 ms | Angular velocity continuous rest time |
| `0x006F` | `WZSTATIC` | `0x012C` | 0.300 deg/s | Angular velocity integration threshold |

### Sample Data From Initial Dump

These values are one sampled moment from the device, not fixed configuration defaults.

| Address | Name | Raw value | Decoded value |
|---:|---|---:|---|
| `0x0034` | `AX` | `0xFF8B` | -0.057 g |
| `0x0035` | `AY` | `0x0005` | 0.002 g |
| `0x0036` | `AZ` | `0x07FF` | 1.000 g |
| `0x0037` | `GX` | `0x0000` | 0.000 deg/s |
| `0x0038` | `GY` | `0x0001` | 0.061 deg/s |
| `0x0039` | `GZ` | `0x0000` | 0.000 deg/s |
| `0x003A` | `HX` | `0x0EC9` | 3785 LSB |
| `0x003B` | `HY` | `0x0179` | 377 LSB |
| `0x003C` | `HZ` | `0xF25B` | -3493 LSB |
| `0x003D`~`0x003E` | `Roll` | `0x0000009D` | 0.157 deg |
| `0x003F`~`0x0040` | `Pitch` | `0x00000CE2` | 3.298 deg |
| `0x0041`~`0x0042` | `Yaw` | `0x0002A88C` | 174.220 deg |
| `0x0043` | `TEMP` | `0x0ACD` | 27.65 C |
| `0x0051` | `q0` | `0x5F34` | 0.743774 |
| `0x0052` | `q1` | `0xFDAA` | -0.018250 |
| `0x0053` | `q2` | `0x02DD` | 0.022369 |
| `0x0054` | `q3` | `0x5578` | 0.667725 |

## Modbus Read Frame

Read command format:

```text
ID 03 ADDRH ADDRL LENH LENL CRCH CRCL
```

Response format:

```text
ID 03 LEN DATA1H DATA1L ... DATAnH DATAnL CRCH CRCL
```

The document examples place CRC as `CRCH CRCL`. Current code matches this order.

Verified against document examples:

```text
Read AX~AZ:
50 03 00 34 00 03 49 84

Unlock:
50 06 00 69 B5 88 22 A1
```

Current code produces the same CRC bytes for these frames.

## Modbus Write Frame

Write command format:

```text
ID 06 ADDRH ADDRL DATAH DATAL CRCH CRCL
```

Response format is the same as the request:

```text
ID 06 ADDRH ADDRL DATAH DATAL CRCH CRCL
```

Important write flow:

1. Write unlock key before write operations.
2. Send the write command within 10 seconds.
3. Save only if the setting must persist.

Unlock register:

- Register: `KEY`
- Address: `0x0069`
- Unlock value: `0xB588`
- Example: `50 06 00 69 B5 88 22 A1`

Save/reboot/reset register:

- Register: `SAVE`
- Address: `0x0000`
- Save value: `0x0000`
- Reboot value: `0x00FF`
- Factory reset value: `0x0001`

Current code behavior:

- `writeReg(regAddr, value)` writes a register without sending `SAVE`.
- `writeReg(regAddr, value, save=True)` writes a register and then sends `SAVE`.
- Use `save=True` only for settings that should remain after power cycle.
- For tests, keep `save=False` so temporary changes are not intentionally persisted.

## Data Registers Used By Current Code

The current loop reads:

```python
self.readReg(0x34, 15)
```

This sends:

```text
ID=0x50, function=0x03, start=0x0034, count=15
```

For the current code settings, the full TX frame is:

```text
50 03 00 34 00 0F 49 81
```

This covers registers `0x34` through `0x42`:

| Address | Name | Meaning | Conversion |
|---:|---|---|---|
| `0x34` | `AX` | Acceleration X | `AX / 32768 * 16g` |
| `0x35` | `AY` | Acceleration Y | `AY / 32768 * 16g` |
| `0x36` | `AZ` | Acceleration Z | `AZ / 32768 * 16g` |
| `0x37` | `GX` | Angular velocity X | `GX / 32768 * 2000 deg/s` |
| `0x38` | `GY` | Angular velocity Y | `GY / 32768 * 2000 deg/s` |
| `0x39` | `GZ` | Angular velocity Z | `GZ / 32768 * 2000 deg/s` |
| `0x3A` | `HX` | Magnetic field X | `HX`, unit LSB |
| `0x3B` | `HY` | Magnetic field Y | `HY`, unit LSB |
| `0x3C` | `HZ` | Magnetic field Z | `HZ`, unit LSB |
| `0x3D` | `LRoll` | Roll low word | Combine with `HRoll` |
| `0x3E` | `HRoll` | Roll high word | `Roll / 1000 deg` |
| `0x3F` | `LPitch` | Pitch low word | Combine with `HPitch` |
| `0x40` | `HPitch` | Pitch high word | `Pitch / 1000 deg` |
| `0x41` | `LYaw` | Yaw low word | Combine with `HYaw` |
| `0x42` | `HYaw` | Yaw high word | `Yaw / 1000 deg` |

Expected response for the current read:

```text
50 03 1E [30 data bytes] CRCH CRCL
```

`0x1E` means 30 bytes, which is 15 registers.

## Document Examples

Read acceleration only:

```text
Send:   50 03 00 34 00 03 49 84
Return: 50 03 06 AXH AXL AYH AYL AZH AZL CRCH CRCL
```

Read angular velocity only:

```text
Send:   50 03 00 37 00 03 B9 84
Return: 50 03 06 GXH GXL GYH GYL GZH GZL CRCH CRCL
```

Read magnetic field only:

```text
Send:   50 03 00 3A 00 03 28 47
Return: 50 03 06 HXH HXL HYH HYL HZH HZL CRCH CRCL
```

Read angles only:

```text
Send:   50 03 00 3D 00 06 59 85
Return: 50 03 0C LRollH LRollL HRollH HRollL LPitchH LPitchL HPitchH HPitchL LYawH LYawL HYawH HYawL CRCH CRCL
```

## Code Review Notes

Current communication framing is consistent with the protocol document:

- Function `0x03` for reads.
- Function `0x06` for single-register writes.
- Default slave address `0x50`.
- Current read start `0x0034`.
- Current read count `15`.
- CRC byte order matches document examples.

One parsing detail needs review:

- The document says magnetic field values `HX`, `HY`, `HZ` are raw LSB values.
- Current code scales magnetic values with `* 13 / 1000`.
- If magnetic output is needed, confirm whether this scaling came from another WITMOTION model or remove it to match this protocol document.

## Bring-up Logs

The code should print Modbus traffic while debugging:

```text
MODBUS READ: slave=0x50, func=0x03, start=0x0034, count=15
TX: 50 03 00 34 00 0F 49 81
RX: 50 03 1E ...
```

If `TX` appears but `RX` does not:

- Check RS485 A/B wiring.
- Check power and GND.
- Check baud rate.
- Check slave address.
- Check whether another program is holding the COM port.

If `RX` appears but parsed values do not print:

- Check slave address in response.
- Check function code.
- Check byte count. For the current read it should be `0x1E`.
- Check CRC.
- Check whether the device model uses a different register map.
