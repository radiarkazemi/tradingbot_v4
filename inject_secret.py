"""
inject_secret.py — Patches the secret into core/license.py and
manage_licenses.py automatically.

Run this ONCE on your developer machine before building the EXE.
Never commit the patched files to git — add them to .gitignore.
The secret itself lives only in this script and in your head.

Usage:
    python inject_secret.py
"""

import os
import re
import hashlib

# ── YOUR SECRET — generated once, never changes ───────────────────
_MY_SECRET_HEX = "eafc4c043540c2036cf70cf9b0aa03c7be3d1bc4f79cd7af7c74bddfdf5706b0a9a59774527cbb0c0b7062c8196c6502e798ce888834057e995ae678aa728967"

# Verification fingerprint — confirms the hex above is correct
_EXPECTED_SHA256 = "5ff23e9cdcad82f56742f2943756e1978afcbcef25517ce6e15cf63954f93b09"

ROOT = os.path.dirname(os.path.abspath(__file__))

FILES_TO_PATCH = [
    os.path.join(ROOT, "core", "license.py"),
    os.path.join(ROOT, "manage_licenses.py"),
]

# The replacement line — uses bytes.fromhex() so no string is visible
REPLACEMENT = f'_SECRET = bytes.fromhex("{_MY_SECRET_HEX}")\n'

# Pattern matches any existing _SECRET assignment
PATTERN = re.compile(r'^_SECRET\s*=.*$', re.MULTILINE)


def verify_secret():
    raw = bytes.fromhex(_MY_SECRET_HEX)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != _EXPECTED_SHA256:
        print("ERROR: Secret hex does not match expected fingerprint.")
        print(f"  Expected: {_EXPECTED_SHA256}")
        print(f"  Got:      {actual}")
        raise SystemExit(1)
    return raw


def patch_file(path: str):
    if not os.path.exists(path):
        print(f"  SKIP (not found): {path}")
        return

    with open(path, encoding="utf-8") as f:
        content = f.read()

    if "_SECRET" not in content:
        print(f"  SKIP (no _SECRET line): {path}")
        return

    new_content = PATTERN.sub(REPLACEMENT.rstrip(), content)

    if new_content == content:
        print(f"  ALREADY PATCHED: {path}")
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"  PATCHED: {path}")


if __name__ == "__main__":
    print()
    print("=" * 55)
    print("  TraderBot v4 — Secret Injector")
    print("=" * 55)
    print()

    # Verify the hex is correct before touching any file
    secret = verify_secret()
    print(f"  Secret verified ✓  ({len(secret)} bytes)")
    print(f"  SHA-256: {_EXPECTED_SHA256[:16]}...")
    print()

    for f in FILES_TO_PATCH:
        patch_file(f)

    print()
    print("  Done. Both files are ready for build.bat.")
    print()
    print("  IMPORTANT: Do NOT commit the patched files to git.")
    print("  Run this script again after any git pull/reset.")
    print("=" * 55)
    print()