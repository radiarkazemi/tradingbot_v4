"""
core/cache_cleanup.py — Stale cache invalidation on version upgrade.

Runs once at GUI startup, before anything else touches session or
undo state. Compares the running APP_VERSION against the last
version that was actually run on this machine (stored in
%APPDATA%/TraderBotV4/last_version.txt).

If they differ — meaning an update just installed — all session
files and undo history from the OLD version are wiped. This
guarantees a freshly-updated bot never resumes mid-cycle state,
rectangle geometry, or lock flags that were computed by logic that
no longer exists in the new version (e.g. the old broken R2 formula,
the old wrong SL direction, etc.) — exactly the kind of bug class
that caused the original SL/R2 issues to look "half-fixed" after an
update.

NOTE: This does NOT touch profile.json (MT5 credentials) or
license.dat — those should survive updates normally.
"""

import os
import glob
import logging
import platform

log = logging.getLogger("cache_cleanup")


def _appdata_dir() -> str:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        folder = os.path.join(base, "TraderBotV4")
    else:
        folder = os.path.join(os.path.expanduser("~"), ".traderbotv4")
    os.makedirs(folder, exist_ok=True)
    return folder


def _last_version_file() -> str:
    return os.path.join(_appdata_dir(), "last_version.txt")


def _read_last_version() -> str:
    try:
        path = _last_version_file()
        if os.path.exists(path):
            return open(path, encoding="utf-8").read().strip()
    except Exception:
        pass
    return ""


def _write_current_version(version: str):
    try:
        with open(_last_version_file(), "w", encoding="utf-8") as f:
            f.write(version)
    except Exception as e:
        log.warning("Failed to write version stamp: %s", e)


def _wipe_stale_caches() -> int:
    """Delete all session_*.json and undo_history.json. Returns count removed."""
    removed = 0
    base = _appdata_dir()

    # Session files (resume-on-restart state)
    sessions_dir = os.path.join(base, "sessions")
    if os.path.isdir(sessions_dir):
        for f in glob.glob(os.path.join(sessions_dir, "session_*.json")):
            try:
                os.remove(f)
                removed += 1
            except Exception as e:
                log.warning("Failed to remove %s: %s", f, e)

    # Undo history
    undo_path = os.path.join(base, "undo_history.json")
    if os.path.exists(undo_path):
        try:
            os.remove(undo_path)
            removed += 1
        except Exception as e:
            log.warning("Failed to remove undo history: %s", e)

    return removed


def check_and_clear_on_update(current_version: str) -> dict:
    """
    Call once at startup. Returns a dict describing what happened:
      {"upgraded": bool, "from": str, "to": str, "files_removed": int}
    """
    last_version = _read_last_version()

    if not last_version:
        # First run ever on this machine — nothing to clear, just stamp.
        _write_current_version(current_version)
        return {
            "upgraded": False, "from": None,
            "to": current_version, "files_removed": 0,
        }

    if last_version == current_version:
        # Same version, normal restart — leave caches alone.
        return {
            "upgraded": False, "from": last_version,
            "to": current_version, "files_removed": 0,
        }

    # Version changed — this is a fresh update. Wipe stale state.
    removed = _wipe_stale_caches()
    _write_current_version(current_version)

    log.info(
        "Detected update %s → %s, cleared %d stale cache file(s)",
        last_version, current_version, removed
    )

    return {
        "upgraded": True, "from": last_version,
        "to": current_version, "files_removed": removed,
    }