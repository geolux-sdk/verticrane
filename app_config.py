# coding:UTF-8
# Persisted dashboard settings and the admin PIN for the hidden /setup page.
#
# Stored in config.json next to this file (gitignored, per-deployment). The PIN is
# kept only as a salted SHA-256 hash, never in plaintext. On a fresh install the
# defaults apply (threshold 0.1 %, 1.0 s window, log level INFO, PIN 01023538099);
# the admin is expected to change the PIN on first use via /setup.
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

DEFAULT_PIN: str = "01023538099"
DEFAULT_LOG_LEVEL: str = "INFO"
DEFAULT_SETTINGS: dict[str, Any] = {
    "slope_threshold_pct": 0.1,   # tilt alarm threshold (%)
    "ma_seconds": 1.0,            # moving-average window for the uprightness metric (s)
    "LOG_LEVEL": DEFAULT_LOG_LEVEL,  # loguru threshold: DEBUG/INFO/WARNING/ERROR
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
            if key in data:
                cfg[key] = data[key]
    return cfg


def save(cfg: dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


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
