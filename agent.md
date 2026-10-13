# Agent Instructions

## Project

An unattended tilt recorder for crane verticality. A **Raspberry Pi Zero 2 W** reads
an **HWT9037-485** 9-axis IMU over Modbus RTU / RS-485, waits for the structure to
settle, and records to a binary file that survives having its power cut mid-write.
The operator collects files over the vehicle's WiFi from a browser.

Three devices go on one crane at **BASE / MIDDLE / TOP**.

The requirements are the source of truth: **[TILT_기록시스템_구현요구사항.md](TILT_기록시스템_구현요구사항.md)**.
Implementation details referenced in code comments are documented in README.md.

| Document | Audience |
|---|---|
| `TILT_기록시스템_구현요구사항.md` | Required hardware, environment, field conditions and core behavior |
| `README.md` | Developers: layout, install, run, HTTP API, format, tests |
| `doc/운영자_안내서.md` | Operators: usage, reading the panel, collecting files |
| `doc/protocol.md` | Sensor register map |

The requirements define required conditions and behavior. README documents the
implementation, interfaces and validation. The operator guide explains field use.

## Layout

The split is deliberate: **the recorder owns the serial port**, so nothing in `dev/`
can run while it does.

**Runtime — what systemd starts (repo root)**

| File | Role |
|---|---|
| `recorder.py` | Recording loop, boot sequence and storage error state (README.md) |
| `ahrs_file.py` | `.dat` format: header, blocks, slot names, recovery, merge (README.md) |
| `stability.py` | Decides when recording may begin (README.md) |
| `filestore.py` | File listing, continuity groups, trash (README.md) |
| `web/` | Operator interface, Flask on 8080 (README.md) |
| `eink_panel.py` | e-paper status display and installation label (README.md) |
| `read_status.py` | Sensor connect and status decode — **recorder imports this** |
| `hwt9037_485.py` | Device model (Modbus register read/write/parse) |
| `port_config.py` | Serial port resolution |
| `app_config.py` | Settings, PIN, central loguru setup (`config.json`) |
| `gdey0154d67.py` | e-paper driver (SSD1681) |

**Developer tools (`dev/`)** — Streamlit dashboard, CSV analysis, sensor
configuration, panel test tools, `.dat` → CSV conversion, and `devmode.sh`,
which stops the recorder, runs a command, and restarts it from a trap.

**PC tools (`pc/`)** — `collect.py` runs on the operator's PC/laptop and collects
recordings through the Pi HTTP API. It uses only the Python standard library.

`tests/` holds software checks, `tests/hardware/` holds hardware probes, and
`tests/tools/` holds synthetic-data demos. Test entrypoints resolve imports from
their own location. SCL3300 is retired; do not restore its driver or tools.

## Working Rules

- **Source comments and runtime log messages in English. Operator-facing UI text
  and the requirements document in Korean.**
- Make focused edits directly without waiting for approval; briefly state what you
  are changing as you go.
- Push to GitHub automatically after committing (standing approval); still show the
  branch, remote, and commit summary.
- Do not commit `__pycache__/`, `*.pyc`, `config.json`, or anything under `data/`.
- Before changing the binary format, read the file-format section of README.md. Header and block
  sizes are asserted at import time in `ahrs_file.py`; if an assert fires, the
  layout table in the document needs updating too.
- Before changing serial protocol logic, check [doc/protocol.md](doc/protocol.md)
  for the register map and conversions. The device serial (`0x7F`~`0x84`) decodes
  **low byte first** — high byte first also yields a plausible string, which is why
  that bug survived a long time.

## Things that are easy to get wrong here

- **Nothing renames a recording, ever.** The name is a rotating slot number and
  says nothing that could later turn out to be wrong; what the recording is lives
  in the header. Putting a fact in a name means rewriting the name when the fact
  changes, and every reader has to agree on the spelling -- one disagreement over
  suffix order once left an intact recording on the card and nothing in the
  operator's list (README.md).
- **The device does not grade its own clock.** The header records what it believed
  when the file opened and claims nothing further. There is no time quality, no
  .unsynced, no sidecar, no retroactive correction, and no NTP watch; they were
  removed because a device cannot check the clock it is grading (README.md). The header
  timestamp is still sound for *ordering* -- one card, one clock, so differences
  hold -- which is why the list and the continuity grouping use it.
- **Never let a missing panel, absent Flask, or a dropped serial link stop the
  recording.** A device that quietly stopped recording is the worst outcome in
  this project.
- **`close()` does not reach the disk; `fsync()` does.** See README.md before changing
  anything about how blocks are written.
- **The panel is drawn on events, not on a timer.** The thread blocks on an
  event with no timeout; whoever changes something calls `refresh_now()`. Only
  the measurement screen also repeats, because its number tracks the crane. The
  panel is rated in refresh count and would be worn out in about two weeks at
  1 Hz (README.md).
- **Nothing starts a recording from the web.** Reaching the page is the condition
  that suppresses auto-recording and discards one in progress, so a start button
  asked for the thing the asker's presence denies (README.md).

## Validation

No hardware needed (runtime dependencies; --all also needs dev dependencies and Node):

```bash
python tests/run.py
python tests/run.py --all
python tests/test_ahrs_file.py     # format: write, tear, recover, merge, slot names
python tests/test_eink_panel.py    # panel: state -> screen, wake scheduling, frames
python tests/test_stability.py     # judge: 0/360 seam, window, motion rejection
python stability.py data/*.csv   # replay recorded logs against the limits
python eink_panel.py --out panel.png --scale 3 --screen boot
```

On the Pi, `./test.sh` runs software checks only. Use
`./dev/devmode.sh ./test.sh --hardware` for sensor checks with service restoration.
Install developer dependencies first (`./install_requirements.sh --dev`). The device is at
`pi@192.168.0.19`; deploy with `git push` then `./update.sh` there.
