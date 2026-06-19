# HWT9073 RS485 Modbus Protocol Notes

This document summarizes the WITMOTION high-precision sensor Modbus protocol as it
applies to the **HWT9037-485** used in this project, organized from the two source PDFs.

## Source Documents

- `doc/HWT9073 RS485 Manual.pdf`
  - 8-page user manual (covers the HWT9053/HWT90xx RS485 family).
  - Intro, warnings, and RS485 wiring. Links out to driver/SDK/protocol resources.
- `doc/High-precision sensor Modbus protocol.pdf`
  - 53-page Modbus protocol document.
  - Full register table, read/write frame formats, conversion formulas, and per-register command examples.

### Model applicability

- The protocol document is written for the **HWTx073** high-precision series.
- Registers marked **【1】** in the table below (`WORKMODE`, `GYROPTP`, `GPTPTIME`,
  `GYROBAIS`, `GBAISTIME`, `GSTATICTHRE`, `GSTATICTIME`, `PGSCALE`, `GSCALERANGE`)
  are noted as *only applicable to HWTx073* and may behave differently on other models.
- Baud codes `0x08` (460800) and `0x09` (921600) are listed as supported only by
  `WT931/JY931/HWT606/HWT906`, i.e. not guaranteed on this unit.

## RS485 Wiring

From the RS485 user manual (MCU connection step):

- `VCC` to `5~36V` (recommended)
- `B` to `B`
- `A` to `A`
- `GND` to `GND`

Warnings from the manual:

- More than 36 V on the supply wiring can permanently damage the sensor.
- Do not connect `VCC` directly to `GND` (burns the board).
- Use the original factory cable/accessories for proper grounding.
- Do not access the I2C interface.

If the serial port opens but no `RX` log appears, check A/B wiring first.

## Usage Procedure (important)

The serial command sequence must complete within **10 seconds**, otherwise the device
**auto-locks**. To modify configuration registers, follow this order:

1. **Unlock** — write `0xB588` to `KEY` (`0x69`).
2. **Read / write** the target configuration register.
3. **Save** — write `0x0000` to `SAVE` (`0x00`) only if the change must persist.

Output/data registers (acceleration, angle, etc.) are read-only and need no unlock.

## Protocol Frame Formats

All values are sent in **hexadecimal** (not ASCII). Each register address, register
count, and data word is two bytes (high byte first). The last two bytes are a standard
Modbus-RTU CRC16, ordered `CRCH CRCL`.

### Read (function `0x03`)

Request:

```text
ID  03  ADDRH ADDRL  LENH LENL  CRCH CRCL
```

Response:

```text
ID  03  LEN  DATA1H DATA1L ... DATAnH DATAnL  CRCH CRCL
```

`LEN` is the byte count of the data payload (= 2 × register count).

### Write single register (function `0x06`)

Request and response have the same shape (the device echoes the request):

```text
ID  06  ADDRH ADDRL  DATAH DATAL  CRCH CRCL
```

## Complete Register Map

Addresses are Modbus register addresses. `R` = read-only, `R/W` = read/write.
**【1】** marks HWTx073-only registers (see Model applicability).

| Hex | Dec | Name | Function | Dir | Default |
|---:|---:|---|---|:---:|---:|
| `0x00` | 0 | `SAVE` | Save / reboot / reset | R/W | `0x0000` |
| `0x01` | 1 | `CALSW` | Calibration mode | R/W | `0x0000` |
| `0x04` | 4 | `BAUD` | Serial port baud rate | R/W | `0x0002` |
| `0x05` | 5 | `AXOFFSET` | Acceleration X offset | R/W | `0x0000` |
| `0x06` | 6 | `AYOFFSET` | Acceleration Y offset | R/W | `0x0000` |
| `0x07` | 7 | `AZOFFSET` | Acceleration Z offset | R/W | `0x0000` |
| `0x08` | 8 | `GXOFFSET` | Angular velocity X zero bias | R/W | `0x0000` |
| `0x09` | 9 | `GYOFFSET` | Angular velocity Y zero bias | R/W | `0x0000` |
| `0x0A` | 10 | `GZOFFSET` | Angular velocity Z zero bias | R/W | `0x0000` |
| `0x0B` | 11 | `HXOFFSET` | Magnetic field X zero bias | R/W | `0x0000` |
| `0x0C` | 12 | `HYOFFSET` | Magnetic field Y zero bias | R/W | `0x0000` |
| `0x0D` | 13 | `HZOFFSET` | Magnetic field Z zero bias | R/W | `0x0000` |
| `0x0E` | 14 | `WORKMODE`【1】 | Z-axis operation mode | R/W | `0x0000` |
| `0x10` | 16 | `GYROPTP`【1】 | Z-axis static peak-to-peak | R/W | `0x0000` |
| `0x11` | 17 | `GPTPTIME`【1】 | Z-axis peak-to-peak acquisition time | R/W | `0x000A` |
| `0x12` | 18 | `GYROBAIS`【1】 | Z-axis zero bias value | R/W | `0x0000` |
| `0x13` | 19 | `GBAISTIME`【1】 | Z-axis zero bias acquisition time | R/W | `0x000A` |
| `0x14` | 20 | `GSTATICTHRE`【1】 | Z-axis static threshold | R/W | `0x0032` |
| `0x15` | 21 | `GSTATICTIME`【1】 | Z-axis stabilization time | R/W | `0x0064` |
| `0x16` | 22 | `PGSCALE`【1】 | Z-axis calibration factor P | R/W | `0x2710` |
| `0x18` | 24 | `GSCALERANGE`【1】 | Z-axis calibration angle | R/W | `0x02D0` |
| `0x1A` | 26 | `IICADDR` | Device (I2C/Modbus) address | R/W | `0x0050` |
| `0x1B` | 27 | `LEDOFF` | Turn off LED light | R/W | `0x0000` |
| `0x1C` | 28 | `MAGRANGX` | Magnetic field X calibration range | R/W | `0x01F4` |
| `0x1D` | 29 | `MAGRANGY` | Magnetic field Y calibration range | R/W | `0x01F4` |
| `0x1E` | 30 | `MAGRANGZ` | Magnetic field Z calibration range | R/W | `0x01F4` |
| `0x1F` | 31 | `BANDWIDTH` | Bandwidth | R/W | `0x0004` |
| `0x20` | 32 | `GYRORANGE` | Gyroscope range | R/W | `0x0003` |
| `0x21` | 33 | `ACCRANGE` | Acceleration range | R/W | `0x0000` |
| `0x22` | 34 | `SLEEP` | Sleep | R/W | `0x0000` |
| `0x23` | 35 | `ORIENT` | Installation direction | R/W | `0x0000` |
| `0x24` | 36 | `AXIS6` | Algorithm (9-axis / 6-axis) | R/W | `0x0000` |
| `0x25` | 37 | `FILTK` | Dynamic (K value) filter | R/W | `0x001E` |
| `0x26` | 38 | `GPSBAUD` | GPS baud rate | R/W | — |
| `0x27` | 39 | `READADDR` | Read register pointer | R/W | — |
| `0x2A` | 42 | `ACCFILT` | Acceleration filter | R/W | `0x01F4` |
| `0x2E` | 46 | `VERSION` | Version number | R | none |
| `0x30` | 48 | `YYMM` | Year / month | R/W | `0x0000` |
| `0x31` | 49 | `DDHH` | Day / hour | R/W | `0x0000` |
| `0x32` | 50 | `MMSS` | Minute / second | R/W | `0x0000` |
| `0x33` | 51 | `MS` | Millisecond | R/W | `0x0000` |
| `0x34` | 52 | `AX` | Acceleration X | R | — |
| `0x35` | 53 | `AY` | Acceleration Y | R | — |
| `0x36` | 54 | `AZ` | Acceleration Z | R | — |
| `0x37` | 55 | `GX` | Angular velocity X | R | — |
| `0x38` | 56 | `GY` | Angular velocity Y | R | — |
| `0x39` | 57 | `GZ` | Angular velocity Z | R | — |
| `0x3A` | 58 | `HX` | Magnetic field X | R | — |
| `0x3B` | 59 | `HY` | Magnetic field Y | R | — |
| `0x3C` | 60 | `HZ` | Magnetic field Z | R | — |
| `0x3D` | 61 | `LRoll` | Roll (X) angle low word | R | — |
| `0x3E` | 62 | `HRoll` | Roll (X) angle high word | R | — |
| `0x3F` | 63 | `LPitch` | Pitch (Y) angle low word | R | — |
| `0x40` | 64 | `HPitch` | Pitch (Y) angle high word | R | — |
| `0x41` | 65 | `LYaw` | Yaw (Z) angle low word | R | — |
| `0x42` | 66 | `HYaw` | Yaw (Z) angle high word | R | — |
| `0x43` | 67 | `TEMP` | Module temperature | R | — |
| `0x51` | 81 | `q0` | Quaternion 0 | R | — |
| `0x52` | 82 | `q1` | Quaternion 1 | R | — |
| `0x53` | 83 | `q2` | Quaternion 2 | R | — |
| `0x54` | 84 | `q3` | Quaternion 3 | R | — |
| `0x61` | 97 | `GYROCALITHR` | Gyro still threshold | R/W | `0x0000` |
| `0x63` | 99 | `GYROCALTIME` | Gyro auto calibration time | R/W | `0x03E8` |
| `0x69` | 105 | `KEY` | Unlock key | R/W | `0x0000` |
| `0x6A` | 106 | `WERROR` | Gyroscope change value | R | `0x0000` |
| `0x6E` | 110 | `WZTIME` | Angular velocity continuous rest time | R/W | `0x01F4` |
| `0x6F` | 111 | `WZSTATIC` | Angular velocity integral threshold | R/W | `0x012C` |
| `0x74` | 116 | `MODDELAY` | RS485 data response delay | R/W | `0x0BB8` |
| `0x7F` | 127 | `NUMBERID1` | Device number bytes 1-2 | R | none |
| `0x80` | 128 | `NUMBERID2` | Device number bytes 3-4 | R | none |
| `0x81` | 129 | `NUMBERID3` | Device number bytes 5-6 | R | none |
| `0x82` | 130 | `NUMBERID4` | Device number bytes 7-8 | R | none |
| `0x83` | 131 | `NUMBERID5` | Device number bytes 9-10 | R | none |
| `0x84` | 132 | `NUMBERID6` | Device number bytes 11-12 | R | none |
| `0x95` | 149 | `LREFROLL` | Roll zero reference low word | R/W | `0x0000` |
| `0x96` | 150 | `HREFROLL` | Roll zero reference high word | R/W | `0x0000` |
| `0x97` | 151 | `LREFPITCH` | Pitch zero reference low word | R/W | `0x0000` |
| `0x98` | 152 | `HREFPITCH` | Pitch zero reference high word | R/W | `0x0000` |

## Output Data Conversions

These are the formulas for the read-only measurement registers, taken directly from
the protocol document. Raw words are signed 16-bit (`int16`); angles combine two words
into a signed 32-bit value.

| Register(s) | Quantity | Conversion | Unit |
|---|---|---|---|
| `0x34`~`0x36` | Acceleration | `AX/32768 * 16` | g |
| `0x37`~`0x39` | Angular velocity | `GX/32768 * 2000` | °/s |
| `0x3A`~`0x3C` | Magnetic field | `HX` (raw value) | LSB |
| `0x3D`~`0x42` | Angle (Roll/Pitch/Yaw) | `((int32)(H<<16) \| L) / 1000` | ° |
| `0x43` | Temperature | `TEMP/100` | °C |
| `0x51`~`0x54` | Quaternion q0..q3 | `q/32768` | — |

Note: `g` is gravitational acceleration (use 9.8 m/s² if converting to SI).

The angle high/low word combination, per the document:

```text
Roll  = ((HRollH<<24)  | (HRollL<<16)  | (LRollH<<8)  | LRollL)  / 1000  °
Pitch = ((HPitchH<<24) | (HPitchL<<16) | (LPitchH<<8) | LPitchL) / 1000  °
Yaw   = ((HYawH<<24)   | (HYawL<<16)   | (LYawH<<8)   | LYawL)   / 1000  °
```

## Configuration Register Reference

### `SAVE` (`0x00`)

Save / reboot / factory reset.

- `0x0000`: save current settings
- `0x00FF`: reboot
- `0x0001`: factory reset
- Example (reboot): `50 06 00 00 00 FF C4 0B`

### `CALSW` (`0x01`) — calibration mode

- `0x00`: normal working mode
- `0x01`: auto-add (acceleration) calibration
- `0x03`: height reset
- `0x04`: set heading angle to zero
- `0x07`: magnetic field calibration (spherical fitting)
- `0x08`: set angle reference
- `0x09`: magnetic field calibration (dual-plane mode)
- Example (heading to zero): `50 06 00 01 00 04 D4 48`

### `BAUD` (`0x04`) — serial baud rate

- `0x01`: 4800 bps
- `0x02`: 9600 bps (default)
- `0x03`: 19200 bps
- `0x04`: 38400 bps
- `0x05`: 57600 bps
- `0x06`: 115200 bps
- `0x07`: 230400 bps
- `0x08`: 460800 bps (only `WT931/JY931/HWT606/HWT906`)
- `0x09`: 921600 bps (only `WT931/JY931/HWT606/HWT906`)
- Example (set 115200): `50 06 00 04 00 06 45 88`

### Offsets `AXOFFSET`~`HZOFFSET` (`0x05`~`0x0D`)

- Acceleration offset = `value / 10000` (g)
- Angular velocity offset = `value / 10000` (°/s)
- Magnetic offsets are raw zero-bias values.
- Example (set Acc-X bias 0.1 g, `0x03E8` = 1000): `50 06 00 05 03 E8 94 F4`

### `IICADDR` (`0x1A`) — device address

- Range `0x01`~`0x7F`, default `0x50`.
- Example (set address `0x02`): `50 06 00 1A 00 02 24 4D`

### `LEDOFF` (`0x1B`)

- `0`: LED on, `1`: LED off.
- Example (LED off): `50 06 00 1B 00 01 35 8C`
- On the tested unit, toggling this had no visible effect (see tested values).

### `MAGRANGX`~`MAGRANGZ` (`0x1C`~`0x1E`)

- Magnetic field calibration range per axis, default 500 (`0x01F4`).

### `BANDWIDTH` (`0x1F`)

- `0x00`: 256 Hz
- `0x01`: 188 Hz
- `0x02`: 98 Hz
- `0x03`: 42 Hz
- `0x04`: 20 Hz (default)
- `0x05`: 10 Hz
- `0x06`: 5 Hz

### `GYRORANGE` (`0x20`)

- Fixed `0x03` = 2000 °/s (cannot be changed).

### `ACCRANGE` (`0x21`)

- `0x00`: ±2g, `0x03`: ±16g.
- Adaptive: internally switches to 16g automatically when acceleration exceeds 2g.
- **On this HWT9037-485 unit the register is fixed at `0x03` (±16g) and cannot be
  changed**, like `GYRORANGE`. Writing `0x00`/`0x01` is ACK'd (Modbus func 0x06 returns
  success) but the value is ignored — an immediate read still returns `0x03`. Verified
  2026-06-19 with RAM-only write, write+reboot (`SAVE`=`0x00FF`), and save+reboot; none
  took effect. Acceleration output is therefore always 16g full-scale (at rest AccZ raw
  ≈ 2050 = 1g), so the fixed `AX/32768*16` conversion stays correct. See
  `compare_accrange.py` for the diagnostic.

### `SLEEP` (`0x22`)

- `0x01`: sleep (any serial data wakes it).

### `ORIENT` (`0x23`)

- `0x00`: horizontal install, `0x01`: vertical install (Y-axis arrow must point up).

### `AXIS6` (`0x24`) — algorithm

- `0x00`: 9-axis (magnetic-field absolute heading)
- `0x01`: 6-axis (integral relative heading)

### `FILTK` (`0x25`) — K-value filter

- Range 1~10000, default 30. Lower = stronger anti-vibration, weaker real-time.

### `ACCFILT` (`0x2A`) — acceleration filter

- Range 1~10000, default 500. Lower = stronger anti-vibration, weaker real-time.

### `VERSION` (`0x2E`)

- Read-only version code: `VERSION = (short)((VH<<8) | VL)`.
- Example read: `50 03 00 2E 00 01 E9 82`

### `GYROCALITHR` (`0x61`) — gyro still threshold

- Threshold = `value / 1000` (°/s). Rule of thumb: `GYROCALITHR = WERROR * 1.2`.
- Used together with `GYROCALTIME`.

### `GYROCALTIME` (`0x63`) — gyro auto-calibration time

- Default 1000 ms (`0x03E8`). When angular change stays below `GYROCALITHR` for this
  time, the sensor treats it as still and zeroes small angular velocity.

### `KEY` (`0x69`) — unlock

- Write `0xB588` to unlock before any config write (other values invalid).
- Example: `50 06 00 69 B5 88 22 A1`

### `WERROR` (`0x6A`)

- Read-only: gyroscope change = `value / 1000 * 180 / π` (°/s). Used to set `GYROCALITHR`.

### `WZTIME` (`0x6E`) / `WZSTATIC` (`0x6F`)

- `WZTIME`: continuous still time, default 500 ms.
- `WZSTATIC`: integral threshold = `value / 1000` (°/s), default 300 (0.3 °/s).
- When angular velocity stays below `WZSTATIC` for `WZTIME`, Z-axis heading stops integrating.

### `MODDELAY` (`0x74`)

- RS485 response delay in µs, default 3000. Modbus-version sensors only.

### `NUMBERID1`~`NUMBERID6` (`0x7F`~`0x84`)

- Read-only 12-byte device serial number (ASCII).

### `LREFROLL`~`HREFPITCH` (`0x95`~`0x98`) — angle zero reference

- 32-bit reference: `(((int)HREF<<16) | LREF) / 1000` (°).
- Example (subtract 2° from roll, `2000` = `0x07D0`): `50 06 00 95 07 D0 97 CB`

## Document Command Examples

Read three-axis acceleration:

```text
Send:   50 03 00 34 00 03 49 84
Return: 50 03 06 AXH AXL AYH AYL AZH AZL CRCH CRCL
```

Read three-axis angular velocity:

```text
Send:   50 03 00 37 00 03 B9 84
Return: 50 03 06 GXH GXL GYH GYL GZH GZL CRCH CRCL
```

Read three-axis magnetic field:

```text
Send:   50 03 00 3A 00 03 28 47
Return: 50 03 06 HXH HXL HYH HYL HZH HZL CRCH CRCL
```

Read three-axis angle:

```text
Send:   50 03 00 3D 00 06 59 85
Return: 50 03 0C LRollH LRollL HRollH HRollL LPitchH LPitchL HPitchH HPitchL LYawH LYawL HYawH HYawL CRCH CRCL
```

Read quaternion:

```text
Send:   50 03 00 51 00 04 18 59
Return: 50 03 08 q0H q0L q1H q1L q2H q2L q3H q3L CRCH CRCL
```

Unlock:

```text
Send:   50 06 00 69 B5 88 22 A1
Return: 50 06 00 69 B5 88 22 A1
```

## How The Current Code Uses This

The driver is `hwt9037_485.py` (pymodbus-based), exercised by `test.py`.

```python
HWT9037_485("COM11", 9600, 0x50, updateData)
```

- Port `COM11`, baud `9600`, Modbus slave `0x50` (matches the document default).
- pymodbus builds the RTU frame and CRC automatically, so no manual CRC table is needed.

The loop reads three blocks:

| Call | Registers | Purpose |
|---|---|---|
| `readReg(0x34, 15)` | `0x34`~`0x42` | Acc + Gyro + Mag + Angle |
| `readReg(0x43, 1)` | `0x43` | Temperature |
| `readReg(0x51, 4)` | `0x51`~`0x54` | Quaternion |

`readReg(0x34, 15)` covers registers `0x34` through `0x42`:

| Address | Name | Meaning | Conversion |
|---:|---|---|---|
| `0x34` | `AX` | Acceleration X | `AX / 32768 * 16g` |
| `0x35` | `AY` | Acceleration Y | `AY / 32768 * 16g` |
| `0x36` | `AZ` | Acceleration Z | `AZ / 32768 * 16g` |
| `0x37` | `GX` | Angular velocity X | `GX / 32768 * 2000 °/s` |
| `0x38` | `GY` | Angular velocity Y | `GY / 32768 * 2000 °/s` |
| `0x39` | `GZ` | Angular velocity Z | `GZ / 32768 * 2000 °/s` |
| `0x3A` | `HX` | Magnetic field X | `HX`, unit LSB |
| `0x3B` | `HY` | Magnetic field Y | `HY`, unit LSB |
| `0x3C` | `HZ` | Magnetic field Z | `HZ`, unit LSB |
| `0x3D`~`0x3E` | `Roll` | Roll low/high word | `((HRoll<<16) \| LRoll) / 1000 °` |
| `0x3F`~`0x40` | `Pitch` | Pitch low/high word | `((HPitch<<16) \| LPitch) / 1000 °` |
| `0x41`~`0x42` | `Yaw` | Yaw low/high word | `((HYaw<<16) \| LYaw) / 1000 °` |

Write flow in code:

- `writeReg(addr, value)` unlocks, then writes, without `SAVE`.
- `writeReg(addr, value, save=True)` unlocks, writes, then sends `SAVE`.
- Use `save=True` only for settings that must survive a power cycle; keep `save=False` for tests.

### Known parsing discrepancy

- The protocol document states magnetic field `HX`/`HY`/`HZ` are **raw LSB** values (no scaling).
- The current code scales magnetic values by `* 13 / 1000`.
- This scaling likely came from another WITMOTION model. Confirm whether it is wanted, or
  remove it to match this protocol document.

## Tested Initial Register Values

The following values were read from the connected device during bring-up. All rows
include the Modbus register address.

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

Conclusion: this hardware appears to have no visible LED, or the common `LEDOFF` register
is not connected to a visible LED on this model.

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

## Bring-up Logs

The code prints Modbus traffic while debugging:

```text
MODBUS READ: slave=0x50, func=0x03, start=0x0034, count=15
```

If a read is sent but no data comes back:

- Check RS485 A/B wiring.
- Check power and GND.
- Check baud rate.
- Check slave address.
- Check whether another program is holding the COM port.

If data returns but parsed values do not print:

- Check slave address in response.
- Check function code.
- Check byte count. For the `0x34` read it should be `0x1E` (30 bytes = 15 registers).
- Check CRC.
- Check whether the device model uses a different register map.
