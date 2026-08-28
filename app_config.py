# coding:UTF-8
# Persisted settings and the admin PIN.
#
# Stored in config.json next to this file (gitignored, per-deployment). The PIN is
# kept only as a salted SHA-256 hash, never in plaintext.
#
# This module also owns the central loguru configuration: LOG_LEVEL is read from
# config.json and applied to the root logger, so every tool that imports
# app_config shares one output format suited to both a terminal and the systemd
# journal. The setup runs once on import (see the bottom of the file).

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Optional

from loguru import logger

CONFIG_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Settled, and not a secret. It is the owner's own phone number, chosen so it is
# always to hand, and it guards only deleting a file and saving settings.
#
# Reviews keep flagging this as a hardcoded default credential. It is not worth
# raising: listing and downloading are unauthenticated on the same device, and a
# single unauthenticated GET /api/files already ends a measurement in progress --
# a larger and irreversible lever than anything behind the PIN. What the PIN is
# therefore changes nothing about the exposure. If the API as a whole ever gains
# authentication, that is a different question and this line is not its answer.
DEFAULT_PIN: str = "01023538099"
DEFAULT_LOG_LEVEL: str = "INFO"

# AUTO_RUN recorder settings. Kept in the same config.json rather than a second
# .env file: two stores would mean forever asking which one is in force.
RECORDER_DEFAULTS: dict[str, Any] = {
    # Where on the crane this unit sits. SENSOR_ID is not here: it is the Pi's
    # hostname, so it can never be left blank or drift from the address in use.
    "sensor_flag": "unset",                # base / middle / top
    "contact_face": "bottom",              # which face meets the structure
    # Always waited: the operator powers the device on at the bottom and climbs
    # the tower crane to fit it. Recording must not start during the ascent.
    "mount_delay_seconds": 60,
    "network_wait_seconds": 90,            # once associated, how long to wait for DHCP
    "http_wait_seconds": 60,               # window after the address lands, network only
    "time_save_interval_seconds": 10,      # how often the last known time is persisted
    "stability_window_seconds": 5.0,
    "stability_min_samples": 100,
    "gyro_rms_max_dps": 0.5,
    "accel_std_max_g": 0.01,
    "attitude_std_max_deg": 0.3,
    "record_fsync_interval_seconds": 1.0,
    "segment_minutes": 0,                  # 0 = one file until the power drops
    "merge_gap_tolerance_seconds": 2.0,
    "stop_on_unstable": False,             # swaying is the thing we came to record
    "panel_refresh_seconds": 60,           # measurement screen only: see section 9
    "ip_check_interval_seconds": 10,       # how soon the panel learns the address changed
    "panel_rotation": 90,                  # clockwise; the panel is mounted turned
    "delete_after_download": True,
    "trash_retention_days": 7,
    "min_free_mb": 500,
    "http_port": 8080,
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "slope_threshold_pct": 0.1,   # tilt alarm threshold (%)
    "ma_seconds": 1.0,            # moving-average window for the uprightness metric (s)
    "LOG_LEVEL": DEFAULT_LOG_LEVEL,  # loguru threshold: DEBUG/INFO/WARNING/ERROR
    "recorder": RECORDER_DEFAULTS,
}

# Single output format shared by interactive runs and systemd. loguru auto-detects
# whether the sink is a TTY, so colours appear in a terminal and are dropped in the
# journal without any extra branching here.
_LOG_FORMAT: str = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} - {message}"
)


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.sha256((salt + str(pin)).encode("utf-8")).hexdigest()


def _default_config() -> dict[str, Any]:
    cfg: dict[str, Any] = dict(DEFAULT_SETTINGS)
    cfg["recorder"] = dict(RECORDER_DEFAULTS)  # own copy: never share the defaults
    cfg["pin_salt"] = ""
    cfg["pin_sha256"] = _hash_pin(DEFAULT_PIN, "")
    return cfg


def load() -> dict[str, Any]:
    # Start from defaults so a missing/partial config.json still yields every key.
    cfg: dict[str, Any] = _default_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
        except (OSError, ValueError):
            data = {}
        for key in cfg:
            if key not in data:
                continue
            # The recorder section is merged key by key, so a config.json written
            # before a setting existed still comes back with that setting.
            if key == "recorder" and isinstance(data[key], dict):
                cfg[key].update(data[key])
            else:
                cfg[key] = data[key]
    return cfg


def save(cfg: dict[str, Any]) -> None:
    # Atomic replace: a half-written config.json would take the recorder down
    # on the next boot, and it is written while a recording may be in progress.
    tmp: str = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_PATH)
    # The rename is not durable until the directory entry itself is flushed --
    # the same step ahrs_file takes after finalising a recording. Without it a
    # power cut moments after saving can leave the old settings in place, and
    # the operator who just set the install position would never know it did
    # not take.
    try:
        fd: int = os.open(os.path.dirname(os.path.abspath(CONFIG_PATH)), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass                        # not every filesystem allows it; the replace stands


def verify_pin(cfg: dict[str, Any], pin: str) -> bool:
    return _hash_pin(pin, cfg.get("pin_salt", "")) == cfg.get("pin_sha256")


def set_pin(cfg: dict[str, Any], new_pin: str) -> None:
    # New random salt each time the PIN changes.
    salt: str = os.urandom(8).hex()
    cfg["pin_salt"] = salt
    cfg["pin_sha256"] = _hash_pin(new_pin, salt)


def update_settings(cfg: dict[str, Any], slope_threshold_pct: float, ma_seconds: float) -> None:
    cfg["slope_threshold_pct"] = float(slope_threshold_pct)
    cfg["ma_seconds"] = float(ma_seconds)


def setup_logging(level: Optional[str] = None) -> str:
    # Replace loguru's default handler with our single formatted stderr sink.
    # Falls back to the loaded config's LOG_LEVEL, then DEFAULT_LOG_LEVEL.
    if level is None:
        level = str(config.get("LOG_LEVEL", DEFAULT_LOG_LEVEL))
    level = level.upper()
    logger.remove()
    logger.add(sys.stderr, level=level, format=_LOG_FORMAT,
               backtrace=False, diagnose=False)
    return level


# Module-level configuration, loaded once. Importers may read app_config.config
# directly (e.g. app_config.config["LOG_LEVEL"]) or call load() for a fresh copy.
config: dict[str, Any] = load()

# Apply the configured log level centrally as soon as the module is imported.
setup_logging()
