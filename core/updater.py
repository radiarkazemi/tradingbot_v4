"""
core/updater.py — Auto-update engine for TraderBot v4.

How it works:
  1. On startup (background thread), fetch VERSION_CHECK_URL
  2. Compare remote version against APP_VERSION
  3. If newer: show a non-blocking notification in the GUI
  4. User clicks "Update Now" → downloads the new installer to a temp
     folder → launches it → exits the current app
     The new installer silently replaces the old one (/SILENT flag).

To publish an update:
  - Build the new installer (build.bat + Inno Setup)
  - Upload it to your server
  - Update version.json on your server:
    {
      "version": "4.1.0",
      "download_url": "https://yoursite.com/TraderBotV4_Setup_v4.1.0.exe",
      "release_notes": "Fixed gap handling, improved lot tables",
      "min_version": "4.0.0"
    }
  That's it — every running copy will notice within 24 hours.
"""

import threading
import urllib.request
import urllib.error
import json
import os
import sys
import tempfile
import subprocess
import logging
from typing import Optional, Callable

log = logging.getLogger("updater")

# ── Configuration ─────────────────────────────────────────────────
# HOST THIS FILE on your server / GitHub / Google Drive public link.
# Change this URL to wherever you'll put your version.json.
VERSION_CHECK_URL = "https://raw.githubusercontent.com/radiarkazemi/tradingbot_v4/main/version.json"

# Current app version — read from version.json so there's a single
# source of truth (publish_update.bat updates version.json, this
# reads it automatically — no need to edit two places).
def _read_local_version() -> str:
    try:
        import json as _json
        # version.json sits next to the EXE (bundled as a data file)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        vpath = os.path.join(base, "version.json")
        if not os.path.exists(vpath):
            # PyInstaller frozen path
            vpath = os.path.join(getattr(sys, "_MEIPASS", base), "version.json")
        with open(vpath, encoding="utf-8") as f:
            return _json.load(f).get("version", "4.0.0")
    except Exception:
        return "4.0.0"

APP_VERSION = _read_local_version()

# How long to wait before giving up on the version check (seconds).
CHECK_TIMEOUT = 8


def _parse_version(v: str):
    """Convert '4.1.2' → (4, 1, 2) for comparison."""
    try:
        return tuple(int(x) for x in str(v).strip().split(".")[:3])
    except Exception:
        return (0, 0, 0)


def _is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


# ── Background check ──────────────────────────────────────────────

class UpdateChecker(threading.Thread):
    """
    Runs once in the background on startup.
    Calls on_update_available(info_dict) on the main thread if a
    newer version exists, or on_check_failed(reason) on error.
    Both callbacks are optional.
    """

    def __init__(self,
                 on_update_available: Optional[Callable] = None,
                 on_check_failed: Optional[Callable] = None,
                 check_url: str = VERSION_CHECK_URL,
                 current_version: str = APP_VERSION):
        super().__init__(daemon=True)
        self._on_available = on_update_available
        self._on_failed    = on_check_failed
        self._url          = check_url
        self._current      = current_version

    def run(self):
        try:
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": f"TraderBotV4/{self._current}"}
            )
            with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            remote_ver = data.get("version", "0.0.0")
            if _is_newer(remote_ver, self._current):
                if self._on_available:
                    self._on_available(data)
            # else: already up to date — silent
        except Exception as e:
            log.debug("Update check failed: %s", e)
            if self._on_failed:
                self._on_failed(str(e))


# ── Downloader ────────────────────────────────────────────────────

class UpdateDownloader(threading.Thread):
    """
    Downloads the installer to a temp file.
    Calls on_progress(bytes_done, total_bytes) periodically.
    Calls on_done(local_path) or on_error(message) when finished.
    """

    def __init__(self, url: str,
                 on_progress: Optional[Callable] = None,
                 on_done: Optional[Callable] = None,
                 on_error: Optional[Callable] = None):
        super().__init__(daemon=True)
        self._url         = url
        self._on_progress = on_progress
        self._on_done     = on_done
        self._on_error    = on_error
        self._cancelled   = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            tmp_dir  = tempfile.mkdtemp(prefix="tbv4_update_")
            filename = self._url.split("/")[-1].split("?")[0] or "TraderBotV4_Update.exe"
            tmp_path = os.path.join(tmp_dir, filename)

            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": f"TraderBotV4/{APP_VERSION}"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                done  = 0
                chunk = 65536  # 64 KB chunks
                with open(tmp_path, "wb") as f:
                    while not self._cancelled:
                        buf = resp.read(chunk)
                        if not buf:
                            break
                        f.write(buf)
                        done += len(buf)
                        if self._on_progress:
                            self._on_progress(done, total)

            if self._cancelled:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return

            if self._on_done:
                self._on_done(tmp_path)

        except Exception as e:
            log.warning("Update download failed: %s", e)
            if self._on_error:
                self._on_error(str(e))


def launch_installer_and_exit(installer_path: str):
    """
    Launch the downloaded installer with /SILENT flag (Inno Setup)
    then exit this process so the installer can replace our files.
    """
    try:
        # /SILENT = show progress but no questions
        # /CLOSEAPPLICATIONS = installer handles closing us
        subprocess.Popen(
            [installer_path, "/SILENT", "/CLOSEAPPLICATIONS"],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    except Exception as e:
        log.error("Failed to launch installer: %s", e)
        return
    # Give the installer a moment to start, then exit
    import time
    time.sleep(1.5)
    sys.exit(0)