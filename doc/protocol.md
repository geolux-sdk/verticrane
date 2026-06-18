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
device_model.DeviceModel("Test Device", "COM11", 115200, 0x50, updateData)
```

Meaning:

- Port: `COM11`
- Baud rate: `115200`
- Modbus slave address: `0x50`

## Device Address

The protocol document says the default Modbus address is `0x50`.

Device address register:

- Register: `IICADDR`
- Address: `0x001A`
- Range: `0x01` to `0x7F`
- Default: `0x0050`

Current code uses `0x50`, which matches the document default.

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
3. Save if the setting must persist.

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
