"""
core/tunnel.py — ngrok tunnel manager for TraderBot v4.

ngrok creates a public HTTPS URL that tunnels to localhost:8000.
Works from any city, any network, no router config needed.

How it works:
  1. Checks if ngrok.exe exists in app data folder
  2. If not, downloads it automatically (single EXE, ~30MB)
  3. Starts ngrok pointing to localhost:8000
  4. Reads the public URL from ngrok's local API
  5. Shows URL in the GUI log panel
  6. Saves URL to %APPDATA%/TraderBotV4/tunnel_url.txt

No account needed for basic use (2 hours per session).
For permanent URLs: sign up free at ngrok.com and add your
authtoken to %APPDATA%/TraderBotV4/ngrok_token.txt
"""

import os
import sys
import json
import time
import shutil
import zipfile
import logging
import platform
import threading
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("tunnel")


# ── Paths ─────────────────────────────────────────────────────────

def _appdata_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "TraderBotV4"
    return Path.home() / ".traderbotv4"


def _ngrok_path() -> Path:
    # Check system PATH first
    found = shutil.which("ngrok") or shutil.which("ngrok.exe")
    if found:
        return Path(found)
    # Our bundled/downloaded copy
    return _appdata_dir() / "ngrok.exe"


def _token_path() -> Path:
    return _appdata_dir() / "ngrok_token.txt"


def _url_cache_path() -> Path:
    return _appdata_dir() / "tunnel_url.txt"


# ── Download ngrok ────────────────────────────────────────────────

def _download_ngrok(dest: Path) -> bool:
    """
    Download ngrok Windows 64-bit from ngrok's CDN.
    It's a zip containing a single ngrok.exe.
    """
    url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
    log.info("Downloading ngrok from %s", url)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_zip = dest.parent / "ngrok_tmp.zip"

        # Download with progress
        req = urllib.request.Request(
            url, headers={"User-Agent": "TraderBotV4/4.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done  = 0
            chunk = 65536
            with open(tmp_zip, "wb") as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if total:
                        pct = done * 100 // total
                        if pct % 20 == 0:
                            log.info("ngrok download: %d%%", pct)

        # Extract ngrok.exe from zip
        with zipfile.ZipFile(tmp_zip) as z:
            for name in z.namelist():
                if name.endswith("ngrok.exe") or name == "ngrok":
                    z.extract(name, dest.parent)
                    extracted = dest.parent / name
                    if extracted != dest:
                        extracted.rename(dest)
                    break

        tmp_zip.unlink(missing_ok=True)
        log.info("ngrok downloaded to %s", dest)
        return dest.exists()

    except Exception as e:
        log.error("Failed to download ngrok: %s", e)
        try:
            tmp_zip.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _ensure_ngrok() -> Optional[Path]:
    p = _ngrok_path()
    if p.exists():
        return p
    log.info("ngrok not found — downloading automatically (~30MB)...")
    if _download_ngrok(p):
        return p
    return None


# ── Authtoken ─────────────────────────────────────────────────────

def _apply_authtoken(ngrok_path: Path):
    """Apply ngrok authtoken if one is saved."""
    tok_file = _token_path()
    if not tok_file.exists():
        return
    token = tok_file.read_text().strip()
    if not token:
        return
    try:
        subprocess.run(
            [str(ngrok_path), "config", "add-authtoken", token],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
                if platform.system() == "Windows" else 0,
        )
        log.info("ngrok authtoken applied")
    except Exception as e:
        log.debug("authtoken apply error: %s", e)


# ── Tunnel manager ────────────────────────────────────────────────

class TunnelManager:
    def __init__(self, port: int = 8000):
        self.port    = port
        self.url: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._ready  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self, on_url=None) -> bool:
        ng = _ensure_ngrok()
        if ng is None:
            log.error("ngrok not available")
            return False
        _apply_authtoken(ng)
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, args=(ng, on_url), daemon=True)
        self._thread.start()
        return True

    def _run(self, ng_path: Path, on_url):
        try:
            cmd = [
                str(ng_path), "http", str(self.port),
                "--log=stdout", "--log-format=json",
            ]
            flags = subprocess.CREATE_NO_WINDOW \
                if platform.system() == "Windows" else 0

            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=flags,
            )

            # Give ngrok time to start and create tunnel
            time.sleep(4.0)

            # Read URL from ngrok's local API (most reliable method)
            url = self._get_url_from_api()

            if not url:
                # Fallback: parse stdout
                url = self._parse_url_from_stdout()

            if url:
                self.url = url
                self._ready.set()
                try:
                    _url_cache_path().write_text(url)
                except Exception:
                    pass
                if on_url:
                    on_url(url)
            else:
                log.error("ngrok started but URL not found")

            # Keep process alive
            if self._proc:
                self._proc.wait()

        except Exception as e:
            log.error("Tunnel error: %s", e)

    def _get_url_from_api(self) -> Optional[str]:
        """Read tunnel URL from ngrok's local REST API."""
        # ngrok v3 always uses 4040, but retry with longer waits
        for attempt in range(15):
            for port in (4040, 4041, 4042):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/tunnels",
                        timeout=3
                    ) as r:
                        data = json.loads(r.read())
                        tunnels = data.get("tunnels", [])
                        # Try https first, then any URL
                        for t in tunnels:
                            url = t.get("public_url", "")
                            if url.startswith("https://"):
                                return url
                        for t in tunnels:
                            url = t.get("public_url", "")
                            if url.startswith("http://"):
                                # Convert http to https
                                return "https://" + url[7:]
                except Exception:
                    pass
            time.sleep(2.0)
        return None

    def _parse_url_from_stdout(self) -> Optional[str]:
        """Fallback: parse URL from ngrok JSON log output."""
        import re
        if not self._proc or not self._proc.stdout:
            return None
        pattern = re.compile(r"https://[a-zA-Z0-9\-]+\.ngrok[-\w]*\.io")
        try:
            for _ in range(30):
                line = self._proc.stdout.readline()
                if not line:
                    break
                m = pattern.search(line)
                if m:
                    return m.group(0)
                # Also try JSON parsing
                try:
                    obj = json.loads(line)
                    url = obj.get("url", "") or obj.get("public_url", "")
                    if url.startswith("https://"):
                        return url
                except Exception:
                    pass
        except Exception:
            pass
        return None

    def wait_for_url(self, timeout: float = 30.0) -> Optional[str]:
        self._ready.wait(timeout)
        return self.url

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self.url = None


# ── Module singleton ──────────────────────────────────────────────
tunnel = TunnelManager(port=8000)


def get_saved_url() -> Optional[str]:
    f = _url_cache_path()
    if f.exists():
        try:
            return f.read_text().strip() or None
        except Exception:
            pass
    return None