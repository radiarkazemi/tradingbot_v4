"""
server.py — TraderBot v4 Remote Control API

Runs a FastAPI HTTP server alongside the bot GUI so you can
control and monitor the bot from your phone via a web browser.

Start it:  python server.py
Then open: http://YOUR-PC-IP:8000  on your phone (same WiFi)
Or use ngrok for internet access: ngrok http 8000

Security: protected by API key in X-API-Key header.
The PWA sends this key automatically once you log in.

Endpoints:
  GET  /           → serve the mobile PWA (index.html)
  GET  /api/status → bot status, balance, positions
  GET  /api/log    → last N log lines
  GET  /api/bias   → latest ICT bias results
  POST /api/start  → start the bot
  POST /api/stop   → stop the bot
  POST /api/settings → update symbol, lot, mode etc.
  GET  /api/positions → open positions with live P&L
  GET  /api/report → trade history summary
"""

from core.license import validate_license, LicenseStatus
from core.profile import load_profile, inject_into_config
from core.watcher import WatcherThread
import config as cfg
import os
import sys
import json
import time
import hmac
import hashlib
import threading
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from collections import deque

# ── FastAPI ───────────────────────────────────────────────────────
try:
    from fastapi import FastAPI, HTTPException, Request, Depends
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.security import APIKeyHeader
    import uvicorn
except ImportError:
    print("ERROR: FastAPI not installed.")
    print("Run: pip install fastapi uvicorn")
    sys.exit(1)

# ── Bot imports ───────────────────────────────────────────────────
from core.tunnel import tunnel as _tunnel, get_saved_url
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


# ── API key — change this or load from profile ────────────────────
# Stored in %APPDATA%/TraderBotV4/api_key.txt

def _get_api_key() -> str:
    key_file = Path(os.environ.get("APPDATA", Path.home())) / \
        "TraderBotV4" / "api_key.txt"
    if key_file.exists():
        return key_file.read_text().strip()
    # Generate and save a new key
    import secrets
    key = secrets.token_urlsafe(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key)
    print(f"\n  API Key generated: {key}")
    print(f"  Saved to: {key_file}\n")
    return key


API_KEY = _get_api_key()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ── Shared state ──────────────────────────────────────────────────

class BotState:
    def __init__(self):
        self.lock = threading.Lock()
        self.worker: Optional[WatcherThread] = None
        self.running = False
        self.log_lines = deque(maxlen=200)
        self.symbol = ""
        self.lot_size = 0.01
        self.lot_mode = 1
        self.risk_free = False
        self.loss_free = False
        self.tp_free = False
        self.balance = 0.0
        self.start_balance = 0.0
        self.target_balance = 0.0
        self.bias_latest = {}
        self.started_at = None

    def add_log(self, msg: str, level: str = "INFO"):
        with self.lock:
            self.log_lines.append({
                "t": datetime.datetime.now().strftime("%H:%M:%S"),
                "m": msg,
                "l": level,
            })

    def get_logs(self, n: int = 50) -> list:
        with self.lock:
            lines = list(self.log_lines)
        return lines[-n:]


state = BotState()


# ── FastAPI app ───────────────────────────────────────────────────

app = FastAPI(title="TraderBot v4 API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_key(key: str = Depends(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return key


# ── Serve PWA ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_pwa():
    pwa_path = ROOT / "mobile" / "index.html"
    if pwa_path.exists():
        return HTMLResponse(pwa_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>PWA not found. Place index.html in mobile/</h1>")


@app.get("/mobile/{filename}")
async def serve_static(filename: str):
    from fastapi.responses import FileResponse
    p = ROOT / "mobile" / filename
    if p.exists():
        return FileResponse(str(p))
    raise HTTPException(404)


# ── Auth endpoint ─────────────────────────────────────────────────

@app.post("/api/auth")
async def auth(request: Request):
    body = await request.json()
    if body.get("key") == API_KEY:
        return {"ok": True}
    raise HTTPException(401, "Invalid key")


# ── Status ────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status(_: str = Depends(verify_key)):
    import MetaTrader5 as mt5
    acct = None
    positions = []
    try:
        if mt5.terminal_info() is not None:
            acct = mt5.account_info()
            raw_pos = mt5.positions_get(symbol=state.symbol) or []
            from config import MAGIC_NUMBER
            for p in raw_pos:
                if p.magic == MAGIC_NUMBER:
                    positions.append({
                        "ticket":  p.ticket,
                        "side":    "BUY" if p.type == 0 else "SELL",
                        "lot":     p.volume,
                        "entry":   p.price_open,
                        "current": p.price_current,
                        "sl":      p.sl,
                        "tp":      p.tp,
                        "pnl":     round(p.profit, 2),
                        "pips":    round(abs(p.price_current - p.price_open) / cfg.PIP_SIZE
                                         if hasattr(cfg, "PIP_SIZE") else 0, 1),
                    })
    except Exception:
        pass

    uptime = ""
    if state.started_at:
        delta = datetime.datetime.now() - state.started_at
        h, m = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(m, 60)
        uptime = f"{h:02d}:{m:02d}:{s:02d}"

    return {
        "running":        state.running,
        "symbol":         state.symbol,
        "lot_size":       state.lot_size,
        "lot_mode":       state.lot_mode,
        "risk_free":      state.risk_free,
        "loss_free":      state.loss_free,
        "tp_free":        state.tp_free,
        "balance":        round(acct.balance, 2) if acct else state.balance,
        "equity":         round(acct.equity,  2) if acct else 0,
        "start_balance":  round(state.start_balance, 2),
        "target_balance": round(state.target_balance, 2),
        "pnl_today":      round((acct.balance - state.start_balance), 2) if acct else 0,
        "positions":      positions,
        "uptime":         uptime,
        "server_time":    datetime.datetime.now().strftime("%H:%M:%S"),
    }


# ── Log ───────────────────────────────────────────────────────────

@app.get("/api/log")
async def get_log(n: int = 50, _: str = Depends(verify_key)):
    return {"lines": state.get_logs(n)}


# ── Bias ──────────────────────────────────────────────────────────

@app.get("/api/bias")
async def get_bias(_: str = Depends(verify_key)):
    bias = state.bias_latest
    if not bias:
        return {"available": False}
    result = {}
    for tf, b in bias.items():
        result[tf] = {
            "direction":  b.direction,
            "bull_pct":   b.bull_pct,
            "bear_pct":   b.bear_pct,
            "confidence": b.confidence,
            "score":      b.score,
        }
    return {"available": True, "data": result}


# ── Start / Stop ──────────────────────────────────────────────────

@app.post("/api/start")
async def start_bot(request: Request, _: str = Depends(verify_key)):
    if state.running:
        return {"ok": False, "msg": "Bot already running"}

    body = await request.json()
    sym = body.get("symbol",   state.symbol or cfg.WATCH_SYMBOL)
    lot = float(body.get("lot_size",  state.lot_size))
    mode = int(body.get("lot_mode",    state.lot_mode))
    rf = bool(body.get("risk_free",  state.risk_free))
    lf = bool(body.get("loss_free",  state.loss_free))
    tp_free = bool(body.get("tp_free",    state.tp_free))

    import MetaTrader5 as mt5
    if not mt5.initialize(login=cfg.MT5_LOGIN,
                          password=cfg.MT5_PASSWORD,
                          server=cfg.MT5_SERVER):
        return {"ok": False, "msg": f"MT5 connect failed: {mt5.last_error()}"}

    acct = mt5.account_info()
    state.balance = acct.balance if acct else 0
    state.start_balance = state.balance
    state.target_balance = state.balance * \
        (1 + getattr(cfg, "BALANCE_TP_RATIO", 0.10))
    state.symbol = sym
    state.lot_size = lot
    state.lot_mode = mode
    state.risk_free = rf
    state.loss_free = lf
    state.tp_free = tp_free
    state.started_at = datetime.datetime.now()

    def _log(msg, level="INFO"):
        state.add_log(msg, level)

    worker = WatcherThread(
        symbol=sym, lot_size=lot,
        risk_free_enabled=rf, loss_free_enabled=lf,
        soft_lot_mode=mode, tp_free=tp_free,
    )
    worker.sig.on_log(_log)

    # Capture bias updates
    def _on_bias(results):
        state.bias_latest = results

    state.worker = worker
    state.running = True
    worker.start()

    state.add_log(f"▶  Bot started | {sym} lot={lot} mode={mode}", "NEW")
    return {"ok": True, "msg": f"Bot started on {sym}"}


@app.post("/api/stop")
async def stop_bot(_: str = Depends(verify_key)):
    if not state.running:
        return {"ok": False, "msg": "Bot not running"}
    if state.worker:
        state.worker.stop()
        state.worker = None
    state.running = False
    state.add_log("■  Bot stopped", "INFO")
    return {"ok": True, "msg": "Bot stopped"}


# ── Settings ──────────────────────────────────────────────────────

@app.post("/api/settings")
async def update_settings(request: Request, _: str = Depends(verify_key)):
    body = await request.json()
    if state.running:
        return {"ok": False, "msg": "Stop the bot before changing settings"}
    if "symbol" in body:
        state.symbol = body["symbol"]
    if "lot_size" in body:
        state.lot_size = float(body["lot_size"])
    if "lot_mode" in body:
        state.lot_mode = int(body["lot_mode"])
    if "risk_free" in body:
        state.risk_free = bool(body["risk_free"])
    if "loss_free" in body:
        state.loss_free = bool(body["loss_free"])
    if "tp_free" in body:
        state.tp_free = bool(body["tp_free"])
    return {"ok": True}


# ── Report ────────────────────────────────────────────────────────

@app.get("/api/report")
async def get_report(_: str = Depends(verify_key)):
    try:
        from core.trade_db import db as trade_db
        stats = trade_db.summary_stats(state.symbol or None)
        daily = trade_db.summary_by_day(state.symbol or None, days=30)
        recent = trade_db.query_trades(
            symbol=state.symbol or None, limit=20)
        return {
            "stats":  stats,
            "daily":  daily,
            "recent": recent,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Entry point ───────────────────────────────────────────────────

def start_server(host: str = "0.0.0.0", port: int = 8000):
    """
    Start the API server + ngrok tunnel in background threads.
    The tunnel gives a public HTTPS URL that works from anywhere
    in the world — no port forwarding, no router config needed.
    """
    # Validate license
    status, _ = validate_license()
    if status != LicenseStatus.OK:
        print(f"ERROR: License invalid ({status}). Cannot start server.")
        return

    # Load profile
    profile = load_profile()
    inject_into_config(profile)
    state.symbol = profile.get("watch_symbol", cfg.WATCH_SYMBOL)
    state.lot_size = float(profile.get("lot_size", 0.01))
    state.lot_mode = int(profile.get("soft_lot_mode", 1))

    # ── Start FastAPI server (auto-finds free port) ───────────────
    import socket as _sock

    def _find_free_port(start: int) -> int:
        for p in range(start, start + 10):
            try:
                with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
                    s.bind(("0.0.0.0", p))
                    return p
            except OSError:
                continue
        return start  # fallback, will fail at bind but with clearer error

    port = _find_free_port(port)

    server_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": host, "port": port, "log_level": "warning"},
        daemon=True,
    )
    server_thread.start()
    time.sleep(1.0)  # brief wait for server to bind

    # ── Start ngrok tunnel ───────────────────────────────────────
    print()
    print("  ============================================================")
    print("    TraderBot v4 — Remote Control")
    print("  ============================================================")
    print(f"  API Key:  {API_KEY}")
    print()

    saved = get_saved_url()
    if saved:
        print(f"  Last URL: {saved}")
        print("  (Starting ngrok tunnel — new URL will appear in ~10 seconds)")
    else:
        print("  Starting ngrok tunnel (downloading ngrok.exe if needed)...")
    print()

    def on_tunnel_url(url: str):
        _save_api_key_file()
        print(f"  ✅  REMOTE URL (works from anywhere in the world):")
        print(f"      {url}")
        print()
        print(f"  API Key: {API_KEY}")
        print()
        print("  1. Open the URL on your phone")
        print("  2. Enter the API key to connect")
        print("  NOTE: Free ngrok URL changes each restart.")
        print("        For permanent URL: add authtoken to")
        print("        %APPDATA%\\TraderBotV4\\ngrok_token.txt")
        print("  ============================================================")
        print()

    _tunnel.port = port
    _tunnel.start(on_url=on_tunnel_url)
    return server_thread


def _save_api_key_file():
    """Save the API key to a readable location for easy copy."""
    try:
        key_dir = Path(os.environ.get("APPDATA", Path.home())) / "TraderBotV4"
        key_dir.mkdir(parents=True, exist_ok=True)
        info_file = key_dir / "remote_access.txt"
        info_file.write_text(
            f"TraderBot v4 — Remote Access Info\n"
            f"===================================\n"
            f"API Key: {API_KEY}\n\n"
            f"Open the URL shown in the bot log on your phone.\n"
            f"Enter the API key to connect.\n"
        )
    except Exception:
        pass


if __name__ == "__main__":
    # Run standalone (headless — no GUI)
    start_server()
    print("  Server running. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Server stopped.")
