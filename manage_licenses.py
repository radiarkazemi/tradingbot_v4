"""
manage_licenses.py — License key generator for TraderBot v4.
Run this on YOUR machine only. Never distribute this file.

Usage:
  python manage_licenses.py generate --user "John Doe" --days 365
  python manage_licenses.py generate --user "John Doe" --never
  python manage_licenses.py verify   --key "TB4V-..."
  python manage_licenses.py list
"""

import sys
import os
import json
import hmac
import uuid
import base64
import hashlib
import datetime
import argparse

# Must match core/license.py exactly
_SECRET = bytes.fromhex("eafc4c043540c2036cf70cf9b0aa03c7be3d1bc4f79cd7af7c74bddfdf5706b0a9a59774527cbb0c0b7062c8196c6502e798ce888834057e995ae678aa728967")

KEYS_FILE = os.path.join(os.path.dirname(__file__), "issued_keys.json")


def _sign(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    sig  = hmac.new(_SECRET, body.encode("utf-8"), hashlib.sha256).hexdigest()
    packet = {"d": body, "s": sig}
    return base64.urlsafe_b64encode(
        json.dumps(packet).encode("utf-8")).decode("ascii")


def _verify(token: str):
    try:
        raw    = base64.urlsafe_b64decode(token.encode("ascii"))
        packet = json.loads(raw)
        body   = packet["d"]
        sig    = packet["s"]
        expected = hmac.new(
            _SECRET, body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False, None
        return True, json.loads(body)
    except Exception:
        return False, None


def generate_key(user: str, days: int = None) -> str:
    """Generate a signed license key for a user."""
    key_id = str(uuid.uuid4())[:8].upper()
    payload = {
        "user":    user,
        "key_id":  key_id,
        "issued":  datetime.date.today().isoformat(),
        "expiry":  (
            "never" if days is None
            else (datetime.date.today() +
                  datetime.timedelta(days=days)).isoformat()
        ),
        "max_devices": 1,
    }
    token = _sign(payload)

    # Save to issued_keys.json
    keys = []
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE) as f:
                keys = json.load(f)
        except Exception:
            keys = []

    keys.append({
        "key_id":  key_id,
        "user":    user,
        "issued":  payload["issued"],
        "expiry":  payload["expiry"],
        "token":   token,
        "revoked": False,
    })
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)

    return token


def cmd_generate(args):
    days = None if args.never else (args.days or 365)
    token = generate_key(args.user, days)
    expiry = "never" if days is None else f"{days} days"

    print()
    print("=" * 60)
    print("  LICENSE KEY GENERATED")
    print("=" * 60)
    print(f"  User   : {args.user}")
    print(f"  Expiry : {expiry}")
    print()
    print("  Key (send this to the user):")
    print()
    print(f"  {token}")
    print()
    print("  Saved to: issued_keys.json")
    print("=" * 60)
    print()


def cmd_verify(args):
    ok, payload = _verify(args.key.strip())
    if not ok:
        print("\n  ❌  INVALID — bad signature or tampered key\n")
        return
    print()
    print("=" * 60)
    print("  KEY VALID")
    print("=" * 60)
    for k, v in payload.items():
        print(f"  {k:12}: {v}")

    expiry = payload.get("expiry", "never")
    if expiry != "never":
        exp = datetime.date.fromisoformat(expiry)
        days_left = (exp - datetime.date.today()).days
        if days_left < 0:
            print(f"\n  ⚠️  EXPIRED {abs(days_left)} days ago")
        else:
            print(f"\n  ✅  {days_left} days remaining")
    else:
        print("\n  ✅  No expiry")
    print("=" * 60)
    print()


def cmd_list(args):
    if not os.path.exists(KEYS_FILE):
        print("\n  No keys issued yet.\n")
        return
    with open(KEYS_FILE) as f:
        keys = json.load(f)
    print()
    print(f"  {'KEY_ID':<10} {'USER':<20} {'ISSUED':<12} {'EXPIRY':<12} {'STATUS'}")
    print("  " + "-" * 65)
    for k in keys:
        status = "REVOKED" if k.get("revoked") else "active"
        expiry = k.get("expiry", "never")
        if expiry != "never":
            exp = datetime.date.fromisoformat(expiry)
            if datetime.date.today() > exp:
                status = "EXPIRED"
        print(f"  {k['key_id']:<10} {k['user']:<20} {k['issued']:<12} {expiry:<12} {status}")
    print()


def cmd_revoke(args):
    if not os.path.exists(KEYS_FILE):
        print("No keys file found.")
        return
    with open(KEYS_FILE) as f:
        keys = json.load(f)
    found = False
    for k in keys:
        if k["key_id"].upper() == args.key_id.upper():
            k["revoked"] = True
            found = True
            print(f"\n  ✅  Key {args.key_id} revoked for {k['user']}\n")
    if not found:
        print(f"\n  ❌  Key ID '{args.key_id}' not found\n")
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TraderBot v4 License Manager")
    sub = parser.add_subparsers(dest="cmd")

    # generate
    gen = sub.add_parser("generate", help="Generate a new license key")
    gen.add_argument("--user",  required=True, help="User's name")
    gen.add_argument("--days",  type=int, default=365,
                     help="Days until expiry (default: 365)")
    gen.add_argument("--never", action="store_true",
                     help="No expiry date")

    # verify
    ver = sub.add_parser("verify", help="Verify a license key")
    ver.add_argument("--key", required=True, help="The license token")

    # list
    sub.add_parser("list", help="List all issued keys")

    # revoke
    rev = sub.add_parser("revoke", help="Revoke a key by ID")
    rev.add_argument("--key-id", required=True, help="The key_id to revoke")

    args = parser.parse_args()
    if args.cmd == "generate": cmd_generate(args)
    elif args.cmd == "verify":  cmd_verify(args)
    elif args.cmd == "list":    cmd_list(args)
    elif args.cmd == "revoke":  cmd_revoke(args)
    else:
        parser.print_help()