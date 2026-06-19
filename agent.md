# Agent Instructions

## Project

This repository contains Python tooling for the Verticrane HWT9037-485 tilt sensor
(9-axis IMU over Modbus RTU / RS-485). It runs on both Windows and Raspberry Pi.

Current primary files:
- `hwt9037_485.py`: device model — pymodbus serial transport, register read/write, parsing, and loop reading.
- `port_config.py`: cross-platform serial-port resolution (`--port` / `VERTICRANE_PORT` / USB auto-detect / platform default).
- `read_status.py`: read and print a decoded device status report.
- `configure_sensor.py`: apply and persist the 6-axis tilt configuration.
- `log_tilt.py`: log tilt data to CSV for a fixed duration, then write an analysis report.
- `analyze_tilt.py`: FFT / sway-spectrum analysis of a logged CSV.
- `dashboard.py`: Streamlit dashboard for browsing logged data (does not open the serial port).
- `test.py`: local entrypoint for opening the device and starting loop reads.

The command-line tools take an optional `--port`; see `doc/raspberry_pi.md` for the
Raspberry Pi setup (USB-RS485 adapter, `dialout` permissions, port selection).

## Working Rules

- Keep source comments and runtime messages in English.
- Before editing code, explain the intended change direction and wait for user approval.
- Do not commit generated Python cache files such as `__pycache__/` or `*.pyc`.
- Keep changes focused on the requested behavior.
- Before changing serial protocol logic, inspect the surrounding packet format and CRC handling.
- Push to GitHub automatically after committing (the user gave standing push approval); still show the branch, remote, and commit summary.

## Validation

Run this after Python source changes:

```powershell
python -m compileall -q .
```
