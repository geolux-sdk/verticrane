# coding:UTF-8
# Persisted dashboard settings and the admin PIN for the hidden /setup page.
#
# Stored in config.json next to this file (gitignored, per-deployment). The PIN is
# kept only as a salted SHA-256 hash, never in plaintext. On a fresh install the
# defaults apply (threshold 0.1 %, 1.0 s window, PIN 01023538099); the admin is
# expected to change the PIN on first use via /setup.

import hashlib
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_PIN = "01023538099"
DEFAULT_SETTINGS = {
    "slope_threshold_pct": 0.1,   # tilt alarm threshold (%)
    "ma_seconds": 1.0,            # moving-average window for the uprightness metric (s)
}


def _hash_pin(pin, salt):
    return hashlib.sha256((salt + str(pin)).encode("utf-8")).hexdigest()


def _default_config():
    cfg = dict(DEFAULT_SETTINGS)
    cfg["pin_salt"] = ""
    cfg["pin_sha256"] = _hash_pin(DEFAULT_PIN, "")
    return cfg


def load():
    # Start from defaults so a missing/partial config.json still yields every key.
    cfg = _default_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        for key in cfg:
            if key in data:
                cfg[key] = data[key]
    return cfg


def save(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def verify_pin(cfg, pin):
    return _hash_pin(pin, cfg.get("pin_salt", "")) == cfg.get("pin_sha256")


def set_pin(cfg, new_pin):
    # New random salt each time the PIN changes.
    salt = os.urandom(8).hex()
    cfg["pin_salt"] = salt
    cfg["pin_sha256"] = _hash_pin(new_pin, salt)


def update_settings(cfg, slope_threshold_pct, ma_seconds):
    cfg["slope_threshold_pct"] = float(slope_threshold_pct)
    cfg["ma_seconds"] = float(ma_seconds)
