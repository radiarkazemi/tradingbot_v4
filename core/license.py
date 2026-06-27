"""
core/license.py — Offline license validation for TraderBot v4.

HOW IT WORKS
────────────
1. You run  manage_licenses.py  on YOUR machine to generate a key.
   The key is a base64-encoded, HMAC-signed JSON payload containing:
     - user name
     - expiry date (or "never")
     - max_devices (always 1 for single-device keys)
     - a unique key_id (UUID)

2. The EXE has the SECRET_KEY baked in (obfuscated, not plain text).
   Without that secret, nobody can generate a valid key.

3. On first run:
   - App shows LicenseDialog asking for the key
   - Key is validated (signature check + expiry check)
   - Machine fingerprint is computed and saved alongside the key
   - App opens

4. On every subsequent run:
   - Key + fingerprint are loaded from disk
   - Signature re-validated
   - Machine fingerprint re-computed and compared to saved one
   - If fingerprint changed (different PC) → rejected
   - If expired → rejected
   - App opens only if all checks pass

MACHINE FINGERPRINT
────────────────────
SHA-256 of: CPU count + platform node (hostname) + platform machine.
Not 100% hardware-locked (changes if user reinstalls Windows and picks
a new hostname) but sufficient to prevent casual copying to a 2nd PC.

LICENSE FILE
────────────
Stored at: %APPDATA%/TraderBotV4/license.dat  (obfuscated JSON)
"""

import os
import json
import hmac
import uuid
import base64
import hashlib
import platform
import datetime
from typing import Tuple, Optional

# ── Secret key — CHANGE THIS before distributing ──────────────────
# This is baked into the EXE. Keep it secret. Use any long random string.
# Generate a new one with: python -c "import secrets; print(secrets.token_hex(32))"
_SECRET = bytes.fromhex("eafc4c043540c2036cf70cf9b0aa03c7be3d1bc4f79cd7af7c74bddfdf5706b0a9a59774527cbb0c0b7062c8196c6502e798ce888834057e995ae678aa728967")

# ── Storage ───────────────────────────────────────────────────────

def _license_dir() -> str:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "TraderBotV4")
    return os.path.join(os.path.expanduser("~"), ".traderbotv4")


def _license_path() -> str:
    return os.path.join(_license_dir(), "license.dat")


# ── Machine fingerprint ───────────────────────────────────────────

def machine_fingerprint() -> str:
    """
    Returns a stable string that identifies this machine.
    Uses: hostname + CPU count + OS platform.
    Stable across reboots; changes if hostname changes.
    """
    parts = [
        platform.node(),                          # hostname
        str(platform.machine()),                  # x86_64 / AMD64
        str(platform.processor() or ""),          # Intel / AMD
        str(os.cpu_count() or 0),                 # core count
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]   # 32-char hex


# ── HMAC signing ─────────────────────────────────────────────────

def _sign(payload: dict) -> str:
    """Sign a dict payload, return base64-encoded token."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    sig  = hmac.new(_SECRET, body.encode("utf-8"), hashlib.sha256).hexdigest()
    packet = {"d": body, "s": sig}
    return base64.urlsafe_b64encode(
        json.dumps(packet).encode("utf-8")).decode("ascii")


def _verify(token: str) -> Tuple[bool, Optional[dict]]:
    """
    Verify a signed token. Returns (True, payload) or (False, None).
    """
    try:
        raw     = base64.urlsafe_b64decode(token.encode("ascii"))
        packet  = json.loads(raw)
        body    = packet["d"]
        sig     = packet["s"]
        expected = hmac.new(
            _SECRET, body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False, None
        return True, json.loads(body)
    except Exception:
        return False, None


# ── Obfuscate license.dat on disk ────────────────────────────────

def _obfuscate(data: str) -> str:
    key  = _SECRET[:16]
    enc  = bytes(b ^ key[i % len(key)] for i, b in enumerate(data.encode()))
    return base64.b64encode(enc).decode("ascii")


def _deobfuscate(data: str) -> str:
    try:
        key  = _SECRET[:16]
        dec  = base64.b64decode(data.encode())
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(dec)).decode()
    except Exception:
        return ""


# ── Public API ────────────────────────────────────────────────────

class LicenseStatus:
    OK          = "ok"
    NOT_FOUND   = "not_found"       # no license file
    INVALID     = "invalid"         # bad signature / tampered
    EXPIRED     = "expired"         # past expiry date
    WRONG_DEVICE = "wrong_device"   # different machine fingerprint


def validate_license() -> Tuple[str, Optional[dict]]:
    """
    Validate the stored license.
    Returns (LicenseStatus.XXX, info_dict_or_None).
    info_dict contains: user, expiry, key_id, activated_at
    """
    path = _license_path()
    if not os.path.exists(path):
        return LicenseStatus.NOT_FOUND, None

    try:
        with open(path) as f:
            stored = json.load(f)
    except Exception:
        return LicenseStatus.INVALID, None

    # Verify signature on the token
    token = stored.get("token", "")
    ok, payload = _verify(token)
    if not ok or not payload:
        return LicenseStatus.INVALID, None

    # Check expiry
    expiry = payload.get("expiry", "never")
    if expiry != "never":
        try:
            exp_date = datetime.date.fromisoformat(expiry)
            if datetime.date.today() > exp_date:
                return LicenseStatus.EXPIRED, payload
        except Exception:
            return LicenseStatus.INVALID, None

    # Check machine fingerprint
    saved_fp = stored.get("fingerprint", "")
    current_fp = machine_fingerprint()
    if saved_fp and saved_fp != current_fp:
        return LicenseStatus.WRONG_DEVICE, payload

    return LicenseStatus.OK, payload


def activate_license(token: str) -> Tuple[bool, str]:
    """
    Activate a license token entered by the user.
    Returns (success, message).
    Saves license.dat on success.
    """
    # Clean up whitespace
    token = token.strip().replace(" ", "").replace("\n", "")

    # Verify signature
    ok, payload = _verify(token)
    if not ok or not payload:
        return False, "Invalid license key. Please check and try again."

    # Check expiry
    expiry = payload.get("expiry", "never")
    if expiry != "never":
        try:
            exp_date = datetime.date.fromisoformat(expiry)
            if datetime.date.today() > exp_date:
                return False, f"This license key expired on {expiry}."
        except Exception:
            return False, "Invalid license key format."

    # Save to disk with machine fingerprint
    fp = machine_fingerprint()
    stored = {
        "token":        token,
        "fingerprint":  fp,
        "activated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        os.makedirs(_license_dir(), exist_ok=True)
        path = _license_path()
        # Obfuscate before saving
        raw = json.dumps(stored)
        with open(path, "w") as f:
            f.write(_obfuscate(raw))
    except Exception as e:
        return False, f"Could not save license: {e}"

    user = payload.get("user", "User")
    return True, f"License activated successfully for {user}."


def get_license_info() -> Optional[dict]:
    """Return license payload if valid, else None."""
    status, info = validate_license()
    if status == LicenseStatus.OK:
        return info
    return None


def license_exists() -> bool:
    return os.path.exists(_license_path())


# ── Override _obfuscate/deobfuscate for the stored file ──────────
# Re-read using deobfuscate

def _load_license_file() -> Optional[dict]:
    path = _license_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            raw = f.read().strip()
        # Try deobfuscated first (new format)
        try:
            plain = _deobfuscate(raw)
            return json.loads(plain)
        except Exception:
            # Fall back to plain JSON (old format)
            return json.loads(raw)
    except Exception:
        return None


# Patch validate_license to use _load_license_file
def validate_license() -> Tuple[str, Optional[dict]]:  # noqa: F811
    stored = _load_license_file()
    if stored is None:
        return LicenseStatus.NOT_FOUND, None

    token = stored.get("token", "")
    ok, payload = _verify(token)
    if not ok or not payload:
        return LicenseStatus.INVALID, None

    expiry = payload.get("expiry", "never")
    if expiry != "never":
        try:
            if datetime.date.today() > datetime.date.fromisoformat(expiry):
                return LicenseStatus.EXPIRED, payload
        except Exception:
            return LicenseStatus.INVALID, None

    saved_fp = stored.get("fingerprint", "")
    if saved_fp and saved_fp != machine_fingerprint():
        return LicenseStatus.WRONG_DEVICE, payload

    return LicenseStatus.OK, payload