"""
core/tunnel.py — ngrok tunnel manager for TraderBot v4.

ngrok creates a public HTTPS URL that tunnels to localhost:8000.
Works from any city, any network, no router config needed.

How it works:
  1. Checks if ngrok.exe exists in app data folder
  2. If not, downloads it automatically (single EXE, ~30MB)
  3. Starts ngrok pointing to localhost:PORT
  4. Reads the public URL from ngrok's local REST API (most reliable)
  5. Falls back to parsing stdout if API is unavailable
  6. Saves URL to %APPDATA%/TraderBotV4/tunnel_url.txt

Supports all ngrok URL formats:
  - https://xxxx.ngrok.io          (older free accounts)
  - https://xxxx.ngrok-free.app    (newer free accounts)
  - https://xxxx.ngrok.app         (paid accounts)
  - Any https:// URL from the API  (future-proof)

No account needed for basic use.
For permanent URLs: add authtoken to %APPDATA%/TraderBotV4/ngrok_token.txt
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
    found = shutil.which("ngrok") or shutil.which("ngrok.exe")
    if found:
        return Path(found)
    return _appdata_dir() / "ngrok.exe"


def _token_path() -> Path:
    return _appdata_dir() / "ngrok_token.txt"


def _url_cache_path() -> Path:
    return _appdata_dir() / "tunnel_url.txt"


# ── Download ngrok ────────────────────────────────────────────────

def _download_ngrok(dest: Path) -> bool:
    url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
    log.info("Downloading ngrok from %s", url)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_zip = dest.parent / "ngrok_tmp.zip"

        req = urllib.request.Request(
            url, headers={"User-Agent": "TraderBotV4"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done  = 0
            with open(tmp_zip, "wb") as f:
                while True:
                    buf = resp.read(65536)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if total and done % (total // 5 + 1) < 65536:
                        log.info("ngrok download: %d%%", done * 100 // total)

        with zipfile.ZipFile(tmp_zip) as z:
            for name in z.namelist():
                if name.lower().endswith("ngrok.exe") or name == "ngrok":
                    data = z.read(name)
                    dest.write_bytes(data)
                    break

        try:
            tmp_zip.unlink()
        except Exception:
            pass

        return dest.exists()
    except Exception as e:
        log.error("Failed to download ngrok: %s", e)
        return False


def _ensure_ngrok() -> Optional[Path]:
    p = _ngrok_path()
    if p.exists():
        return p
    log.info("ngrok not found — downloading automatically (~30MB)...")
    if _download_ngrok(p):
        return p
    return None


def _apply_authtoken(ngrok_path: Path):
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


# ── URL detection ─────────────────────────────────────────────────

def _is_valid_ngrok_url(url: str) -> bool:
    """Accept any HTTPS URL from ngrok — covers all current and future domains."""
    return bool(url and url.startswith("https://"))


def _get_url_from_api(port_start: int = 4040, max_wait: float = 30.0) -> Optional[str]:
    """
    Poll ngrok's local REST API until a tunnel URL appears.
    Tries ports 4040-4042 (ngrok may use any of these).
    More robust than stdout parsing — works regardless of log format.
    """
    deadline = time.time() + max_wait
    attempt  = 0
    while time.time() < deadline:
        for port in (4040, 4041, 4042, 4043):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/tunnels",
                    timeout=2
                ) as r:
                    data    = json.loads(r.read())
                    tunnels = data.get("tunnels", [])
                    # Prefer HTTPS tunnel
                    for t in tunnels:
                        url = t.get("public_url", "")
                        if _is_valid_ngrok_url(url):
                            return url
                    # If only HTTP tunnels listed, convert first one
                    for t in tunnels:
                        url = t.get("public_url", "")
                        if url.startswith("http://"):
                            return "https://" + url[7:]
            except Exception:
                pass
        attempt += 1
        time.sleep(1.5 if attempt < 5 else 2.5)
    return None


# ── Tunnel manager ────────────────────────────────────────────────

class TunnelManager:
    def __init__(self, port: int = 8000):
        self.port    = port
        self.url: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._ready  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._needs_auth = False

    def start(self, on_url=None) -> bool:
        ng = _ensure_ngrok()
        if ng is None:
            log.error("ngrok not available — tunnel not started")
            return False
        _apply_authtoken(ng)
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, args=(ng, on_url), daemon=True)
        self._thread.start()
        return True

    def _run(self, ng_path: Path, on_url):
        try:
            cmd = [str(ng_path), "http", str(self.port), "--no-autoupdate"]
            flags = subprocess.CREATE_NO_WINDOW \
                if platform.system() == "Windows" else 0

            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=flags,
            )

            # Brief startup pause then poll the API
            time.sleep(2.0)

            url = _get_url_from_api(max_wait=30.0)

            if url:
                self.url = url
                self._ready.set()
                try:
                    _url_cache_path().write_text(url)
                except Exception:
                    pass
                if on_url:
                    on_url(url)
                log.info("Tunnel URL: %s", url)
            else:
                # Read ngrok output to show meaningful error
                ngrok_output = []
                try:
                    import select as _sel
                    # Read any buffered output (non-blocking)
                    while True:
                        line = self._proc.stdout.readline()
                        if not line:
                            break
                        ngrok_output.append(line.strip())
                        if len(ngrok_output) > 20:
                            break
                except Exception:
                    pass

                # Look for auth error in output
                full_out = " ".join(ngrok_output).lower()
                if "auth" in full_out or "account" in full_out or "signup" in full_out or "token" in full_out:
                    if on_url:
                        on_url(None)   # signal failure
                    log.error(
                        "ngrok requires an authtoken (free account).\n"
                        "  1. Sign up free at https://ngrok.com\n"
                        "  2. Go to dashboard → Your Authtoken\n"
                        "  3. Copy the token and paste it into:\n"
                        "     %%APPDATA%%\\TraderBotV4\\ngrok_token.txt\n"
                        "  4. Restart the bot"
                    )
                    self._needs_auth = True
                else:
                    log.error(
                        "ngrok started but URL not found after 30s.\n"
                        "  ngrok output: %s\n"
                        "  If this keeps happening, add authtoken from ngrok.com to:\n"
                        "  %%APPDATA%%\\TraderBotV4\\ngrok_token.txt",
                        " | ".join(ngrok_output[:5]) if ngrok_output else "(none)"
                    )

            # Keep process alive
            if self._proc:
                self._proc.wait()

        except Exception as e:
            log.error("Tunnel error: %s", e)

    def wait_for_url(self, timeout: float = 35.0) -> Optional[str]:
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