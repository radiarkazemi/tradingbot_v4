"""
core/profile.py — User profile / credentials storage for TraderBot v4.

Stores MT5 login credentials and user preferences in a JSON file
at %APPDATA%/TraderBotV4/profile.json (Windows) or
~/.traderbotv4/profile.json (other platforms).

Credentials are obfuscated (base64 + XOR with a machine key) so they
are NOT stored in plain text, but this is NOT bank-grade encryption —
it is intended to prevent casual shoulder-surfing only. The profile
file should not be shared.
"""

import os
import json
import base64
import hashlib
import platform
from typing import Optional

# ── Profile file location ─────────────────────────────────────────


def _profile_dir() -> str:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "TraderBotV4")
    return os.path.join(os.path.expanduser("~"), ".traderbotv4")


def _profile_path() -> str:
    return os.path.join(_profile_dir(), "profile.json")


# ── Machine key (non-secret, just ties the file to this machine) ──

def _machine_key() -> bytes:
    """Derive a stable per-machine key from hostname + username."""
    seed = platform.node() + os.environ.get("USERNAME", os.environ.get("USER", "user"))
    return hashlib.sha256(seed.encode()).digest()


def _obfuscate(plaintext: str) -> str:
    """XOR-obfuscate then base64-encode. Not encryption, just not plain text."""
    key = _machine_key()
    data = plaintext.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.b64encode(xored).decode("ascii")


def _deobfuscate(encoded: str) -> str:
    """Reverse of _obfuscate."""
    try:
        key = _machine_key()
        xored = base64.b64decode(encoded.encode("ascii"))
        data = bytes(b ^ key[i % len(key)] for i, b in enumerate(xored))
        return data.decode("utf-8")
    except Exception:
        return ""


# ── Profile schema ────────────────────────────────────────────────

DEFAULT_PROFILE = {
    "display_name":  "",
    "mt5_login":     "",
    "mt5_password":  "",   # stored obfuscated
    "mt5_server":    "",
    "watch_symbol":  "EURUSD",
    "lot_size":      0.01,
    "soft_lot_mode": 1,
    "_version":      1,
}


def profile_exists() -> bool:
    return os.path.exists(_profile_path())


def load_profile() -> dict:
    """Load profile from disk. Returns DEFAULT_PROFILE copy if missing."""
    path = _profile_path()
    if not os.path.exists(path):
        return dict(DEFAULT_PROFILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Deobfuscate password
        if data.get("mt5_password"):
            data["mt5_password"] = _deobfuscate(data["mt5_password"])
        # Fill any missing keys from defaults
        for k, v in DEFAULT_PROFILE.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return dict(DEFAULT_PROFILE)


def save_profile(profile: dict) -> bool:
    """Save profile to disk. Returns True on success."""
    try:
        os.makedirs(_profile_dir(), exist_ok=True)
        to_save = dict(profile)
        # Obfuscate password before writing
        if to_save.get("mt5_password"):
            to_save["mt5_password"] = _obfuscate(to_save["mt5_password"])
        path = _profile_path()
        # Write to temp then rename — atomic on Windows too
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
        return True
    except Exception as e:
        return False


def inject_into_config(profile: dict):
    """
    Write loaded profile values into the live config module so the
    rest of the bot (watcher, detectors, etc.) picks them up without
    needing to import core.profile everywhere.
    """
    import config as cfg
    try:
        cfg.MT5_LOGIN = int(profile.get("mt5_login", 0))
    except (ValueError, TypeError):
        cfg.MT5_LOGIN = 0
    cfg.MT5_PASSWORD = profile.get("mt5_password", "")
    cfg.MT5_SERVER = profile.get("mt5_server",   "")
    cfg.WATCH_SYMBOL = profile.get("watch_symbol", "EURUSD")
    cfg.LOT_SIZE = float(profile.get("lot_size", 0.01))
    cfg.SOFT_LOT_MODE = int(profile.get("soft_lot_mode", 1))
