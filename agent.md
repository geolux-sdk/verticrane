# Agent Instructions

## Project

This repository contains a small Python serial device model for Verticrane.

Current primary files:
- `device_model.py`: serial communication, Modbus CRC, register read/write, and loop reading logic.
- `test.py`: simple local test entrypoint for opening the device and starting loop reads.

## Working Rules

- Keep source comments and runtime messages in English.
- Before editing code, explain the intended change direction and wait for user approval.
- Do not commit generated Python cache files such as `__pycache__/` or `*.pyc`.
- Keep changes focused on the requested behavior.
- Before changing serial protocol logic, inspect the surrounding packet format and CRC handling.
- Before pushing to GitHub, show the branch, remote, commit summary, and wait for explicit push approval.

## Validation

Run this after Python source changes:

```powershell
python -m compileall -q .
```
