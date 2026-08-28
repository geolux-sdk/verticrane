# Agent Instructions

## Project

An unattended tilt recorder for crane verticality. A **Raspberry Pi Zero 2 W** reads
an **HWT9037-485** 9-axis IMU over Modbus RTU / RS-485, waits for the structure to
settle, and records to a binary file that survives having its power cut mid-write.
The operator collects files over the vehicle's WiFi from a browser.

Three devices go on one crane at **BASE / MIDDLE / TOP**.

The requirements are the source of truth: **[TILT_기록시스템_구현요구사항.md](TILT_기록시스템_구현요구사항.md)**.
Section numbers referenced in code comments point there.

| Document | Audience |
|---|---|
| `TILT_기록시스템_구현요구사항.md` | Design rationale. Read before changing behaviour |
| `README.md` | Developers: layout, install, run, HTTP API, format, tests |
| `doc/운영자_안내서.md` | Operators: usage, reading the panel, collecting files |
| `doc/protocol.md` | Sensor register map |

Behaviour changes usually touch three of these. The requirements say why, the
README says what the interface is, and the operator guide says what someone in a
crane basket sees.

## Layout

The split is deliberate: **the recorder owns the serial port**, so nothing in `dev/`
can run while it does.

**Runtime — what systemd starts (repo root)**

| File | Role |
|---|---|
| `recorder.py` | Recording loop and the five-state boot sequence (§3) |
| `ahrs_file.py` | `.dat` format: header, blocks, slot names, recovery, merge (§5) |
| `stability.py` | Decides when recording may begin (§4) |
| `filestore.py` | File listing, continuity groups, trash (§5.4, §7) |
| `web/` | Operator interface, Flask on 8080 (§7, §8) |
| `eink_panel.py` | e-paper status display and installation label (§9) |
| `read_status.py` | Sensor connect and status decode — **recorder imports this** |
| `hwt9037_485.py` | Device model (Modbus register read/write/parse) |
| `port_config.py` | Serial port resolution |
| `app_config.py` | Settings, PIN, central loguru setup (`config.json`) |
| `gdey0154d67.py` | e-paper driver (SSD1681) |

**Developer tools (`dev/`)** — Streamlit dashboard, CSV analysis, sensor
configuration, panel test tools, `.dat` → CSV conversion, and `devmode.sh`,
which stops the recorder, runs a command, and restarts it from a trap.

`dev/` scripts sit one level down, so each puts the repo root on `sys.path` in two
explicit lines before importing runtime modules.

## Working Rules

- **Source comments and runtime log messages in English. Operator-facing UI text
  and the requirements document in Korean.**
- Make focused edits directly without waiting for approval; briefly state what you
  are changing as you go.
- Push to GitHub automatically after committing (standing approval); still show the
  branch, remote, and commit summary.
- Do not commit `__pycache__/`, `*.pyc`, `config.json`, or anything under `data/`.
- Before changing the binary format, read §5 of the requirements. Header and block
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
  operator's list (§5.1).
- **The device does not grade its own clock.** The header records what it believed
  when the file opened and claims nothing further. There is no time quality, no
  .unsynced, no sidecar, no retroactive correction, and no NTP watch; they were
  removed because a device cannot check the clock it is grading (§3). The header
  timestamp is still sound for *ordering* -- one card, one clock, so differences
  hold -- which is why the list and the continuity grouping use it.
- **Never let a missing panel, absent Flask, or a dropped serial link stop the
  recording.** A device that quietly stopped recording is the worst outcome in
  this project.
- **`close()` does not reach the disk; `fsync()` does.** See §6 before changing
  anything about how blocks are written.
- **The panel is drawn on events, not on a timer.** The thread blocks on an
  event with no timeout; whoever changes something calls `refresh_now()`. Only
  the measurement screen also repeats, because its number tracks the crane. The
  panel is rated in refresh count and would be worn out in about two weeks at
  1 Hz (§9).
- **Nothing starts a recording from the web.** Reaching the page is the condition
  that suppresses auto-recording and discards one in progress, so a start button
  asked for the thing the asker's presence denies (§3).

## Validation

No hardware needed:

```bash
python test_ahrs_file.py     # format: write, tear, recover, merge, slot names
python test_eink_panel.py    # panel: state -> screen, wake scheduling, frames
python test_stability.py     # judge: 0/360 seam, window, motion rejection
python stability.py data/*.csv   # replay recorded logs against the limits
python eink_panel.py --out panel.png --scale 3 --screen boot
```

On the Pi (`./test.sh` runs all of it, sensor included). The device is at
`pi@192.168.0.19`; deploy with `git push` then `./update.sh` there.
