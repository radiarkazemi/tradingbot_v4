"""
core/undo_manager.py — Undo / state-restore system for TraderBot v4.

Every 30 seconds (while the bot is running), a full snapshot of every
active SourceState is written to a rolling history file. The GUI's
Undo button restores the most recent PREVIOUS snapshot — reverting
rectangle position, SL, TP, round/touch counters, lot sizes, and
protection toggle states — and re-applies that SL/TP to the live MT5
positions so the broker-side state matches exactly.

History file: %APPDATA%/TraderBotV4/undo_history.json
Keeps the last UNDO_HISTORY_DEPTH snapshots (default 10 = 5 minutes
of history at the 30s interval).

This is intentionally a SEPARATE file from session_*.json (which is
the resume-on-restart mechanism) — undo history is short-lived
in-memory-style state for "oops, fix that" use, not for crash
recovery across bot restarts.
"""

import os
import json
import time
import logging
import platform
import threading
from collections import deque
from typing import Optional, Dict, Any, List

log = logging.getLogger("undo_manager")

UNDO_HISTORY_DEPTH = 10     # number of snapshots to keep
SNAPSHOT_INTERVAL_SEC = 30  # auto-save cadence


# ── Paths ─────────────────────────────────────────────────────────

def _undo_file() -> str:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        folder = os.path.join(base, "TraderBotV4")
    else:
        folder = os.path.join(os.path.expanduser("~"), ".traderbotv4")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "undo_history.json")


# ── Snapshot serialization ──────────────────────────────────────────

def _snapshot_source(state) -> Dict[str, Any]:
    """Capture every field needed to fully restore one SourceState."""
    return {
        "name":              state.name,
        "symbol":            state.symbol,
        "rect_top":          state.rect_top,
        "rect_bottom":       state.rect_bottom,
        "base_lot":          state.base_lot,
        "soft_lot_mode":     state.soft_lot_mode,
        "round":             state.round,
        "touch_count":       state.touch_count,
        "buy_lot":           state.buy_lot,
        "sell_lot":          state.sell_lot,
        "buy_pos_ticket":    state.buy_pos_ticket,
        "sell_pos_ticket":   state.sell_pos_ticket,
        "buy_ticket":        state.buy_ticket,
        "sell_ticket":       state.sell_ticket,
        "cumulative_loss":   getattr(state, "cumulative_loss", 0.0),
        "loss_free_applied": dict(getattr(state, "loss_free_applied", {})),
        "risk_free_applied": dict(getattr(state, "risk_free_applied", {})),
        "buy_r_frozen":      getattr(state, "buy_r_frozen", 0.0),
        "sell_r_frozen":     getattr(state, "sell_r_frozen", 0.0),
        "buy_tp_frozen":     getattr(state, "buy_tp_frozen", 0.0),
        "sell_tp_frozen":    getattr(state, "sell_tp_frozen", 0.0),
        # Live SL/TP at snapshot time — used to re-apply on undo
        "live_buy_sl":       _live_sl_tp(state, "buy",  "sl"),
        "live_buy_tp":       _live_sl_tp(state, "buy",  "tp"),
        "live_sell_sl":      _live_sl_tp(state, "sell", "sl"),
        "live_sell_tp":      _live_sl_tp(state, "sell", "tp"),
        # Protection toggles — captured so undo also restores GUI state
        "risk_free_enabled":      getattr(state, "_risk_free_enabled", False),
        "loss_free_enabled":      getattr(state, "_loss_free_enabled", False),
        "trailing_enabled":       getattr(state, "_trailing_enabled", False),
        "partial_exit_r3_enabled": getattr(state, "_partial_exit_r3_enabled", False),
        "entry_filter_ob_fvg":    getattr(state, "_entry_filter_ob_fvg", False),
        "tp_free":                getattr(state, "tp_free", False),
    }


def _live_sl_tp(state, side: str, field: str) -> Optional[float]:
    """Read the live SL or TP from MT5 for the given side, if open."""
    try:
        import MetaTrader5 as mt5
        ticket = state.buy_pos_ticket if side == "buy" else state.sell_pos_ticket
        if not ticket:
            return None
        positions = mt5.positions_get(symbol=state.symbol) or []
        pos = next((p for p in positions if p.ticket == ticket), None)
        if not pos:
            return None
        return getattr(pos, field, None)
    except Exception:
        return None


# ── Undo manager ──────────────────────────────────────────────────

class UndoManager:
    """
    Owns the rolling snapshot history and the auto-save timer thread.
    One instance lives on the GUI, fed by the running WatcherThread's
    active sources dict.
    """

    def __init__(self):
        self.history: deque = deque(maxlen=UNDO_HISTORY_DEPTH)
        self._lock = threading.Lock()
        self._timer_thread: Optional[threading.Thread] = None
        self._running = False
        self._get_sources_fn = None   # callable returning {name: SourceState}
        self._log_fn = None           # callable(msg, level) for GUI log

        self._load_from_disk()

    def start(self, get_sources_fn, log_fn=None):
        """Begin the 30-second auto-snapshot timer."""
        self._get_sources_fn = get_sources_fn
        self._log_fn = log_fn
        self._running = True
        self._timer_thread = threading.Thread(
            target=self._run_loop, daemon=True)
        self._timer_thread.start()

    def stop(self):
        self._running = False

    def _run_loop(self):
        # Take an initial snapshot shortly after start, then every
        # SNAPSHOT_INTERVAL_SEC seconds after that.
        time.sleep(3)
        while self._running:
            try:
                self.take_snapshot()
            except Exception as e:
                log.warning("Auto-snapshot failed: %s", e)
            for _ in range(SNAPSHOT_INTERVAL_SEC):
                if not self._running:
                    return
                time.sleep(1)

    def take_snapshot(self, label: str = "auto"):
        """Capture current state of all active sources."""
        if not self._get_sources_fn:
            return
        sources = self._get_sources_fn()
        if not sources:
            return

        snapshot = {
            "timestamp": time.time(),
            "label":     label,
            "sources":   {
                name: _snapshot_source(state)
                for name, state in sources.items()
            },
        }

        with self._lock:
            self.history.append(snapshot)
            self._save_to_disk()

        if self._log_fn and label == "manual":
            self._log_fn("💾  Snapshot saved", "INFO")

    def can_undo(self) -> bool:
        with self._lock:
            return len(self.history) >= 2

    def undo(self) -> bool:
        """
        Restore the PREVIOUS snapshot (the one before the most recent).
        Returns True if a restore was performed.
        """
        with self._lock:
            if len(self.history) < 2:
                if self._log_fn:
                    self._log_fn(
                        "⚠️  Nothing to undo — not enough history yet",
                        "WARN")
                return False
            # Drop the current (latest) snapshot, restore the one before it
            self.history.pop()
            target = self.history[-1]

        sources = self._get_sources_fn() if self._get_sources_fn else {}
        restored = 0

        for name, snap in target["sources"].items():
            state = sources.get(name)
            if not state:
                continue
            self._restore_source(state, snap)
            restored += 1

        self._save_to_disk()

        if self._log_fn:
            age_sec = int(time.time() - target["timestamp"])
            self._log_fn(
                f"↩️  Undo complete — restored {restored} source(s) to "
                f"state from {age_sec}s ago", "NEW")
        return True

    def _restore_source(self, state, snap: Dict[str, Any]):
        """Apply a saved snapshot back onto a live SourceState + MT5."""
        # ── Restore in-memory bookkeeping fields ────────────────────
        state.rect_top          = snap["rect_top"]
        state.rect_bottom       = snap["rect_bottom"]
        state.base_lot          = snap["base_lot"]
        state.soft_lot_mode     = snap["soft_lot_mode"]
        state.round             = snap["round"]
        state.touch_count       = snap["touch_count"]
        state.buy_lot           = snap["buy_lot"]
        state.sell_lot          = snap["sell_lot"]
        state.cumulative_loss   = snap["cumulative_loss"]
        state.loss_free_applied = dict(snap["loss_free_applied"])
        state.risk_free_applied = dict(snap["risk_free_applied"])
        state.buy_r_frozen      = snap["buy_r_frozen"]
        state.sell_r_frozen     = snap["sell_r_frozen"]
        state.buy_tp_frozen     = snap["buy_tp_frozen"]
        state.sell_tp_frozen    = snap["sell_tp_frozen"]

        # Protection toggles
        state._risk_free_enabled       = snap.get("risk_free_enabled", False)
        state._loss_free_enabled       = snap.get("loss_free_enabled", False)
        state._trailing_enabled        = snap.get("trailing_enabled", False)
        state._partial_exit_r3_enabled = snap.get("partial_exit_r3_enabled", False)
        state._entry_filter_ob_fvg     = snap.get("entry_filter_ob_fvg", False)
        state.tp_free                  = snap.get("tp_free", False)

        # ── Re-apply live SL/TP to MT5 if the positions still exist ─
        try:
            import MetaTrader5 as mt5
            if snap["buy_pos_ticket"]:
                pos = next((p for p in (mt5.positions_get(symbol=state.symbol) or [])
                           if p.ticket == snap["buy_pos_ticket"]), None)
                if pos:
                    if snap["live_buy_sl"] is not None:
                        state._move_position_sl(pos.ticket, snap["live_buy_sl"])
                    if snap["live_buy_tp"] is not None:
                        state._move_position_tp(pos.ticket, snap["live_buy_tp"])

            if snap["sell_pos_ticket"]:
                pos = next((p for p in (mt5.positions_get(symbol=state.symbol) or [])
                           if p.ticket == snap["sell_pos_ticket"]), None)
                if pos:
                    if snap["live_sell_sl"] is not None:
                        state._move_position_sl(pos.ticket, snap["live_sell_sl"])
                    if snap["live_sell_tp"] is not None:
                        state._move_position_tp(pos.ticket, snap["live_sell_tp"])
        except Exception as e:
            log.warning("Undo: failed to re-apply live SL/TP: %s", e)

        # ── Recreate pending stop orders that were cancelled ─────────
        # If the snapshot had a buy_ticket/sell_ticket (a live pending
        # order at snapshot time) but that ticket no longer exists in
        # MT5's pending orders now, the order was cancelled or filled
        # since the snapshot. If it was CANCELLED (not filled — i.e.
        # there's still no open position for that side), recreate it
        # at the same entry/SL/TP/lot it had.
        try:
            import MetaTrader5 as mt5
            pending = {o.ticket for o in (mt5.orders_get(symbol=state.symbol) or [])}
            open_buy  = state.buy_pos_ticket  is not None
            open_sell = state.sell_pos_ticket is not None

            if (snap.get("buy_ticket") and snap["buy_ticket"] not in pending
                    and not open_buy):
                new_ticket = self._recreate_pending(state, snap, is_buy=True)
                if new_ticket:
                    state.buy_ticket = new_ticket
                    if self._log_fn:
                        self._log_fn(
                            f"↩️  Undo: recreated cancelled BUY-STOP "
                            f"#{new_ticket} for [{state.name[:20]}]", "NEW")

            if (snap.get("sell_ticket") and snap["sell_ticket"] not in pending
                    and not open_sell):
                new_ticket = self._recreate_pending(state, snap, is_buy=False)
                if new_ticket:
                    state.sell_ticket = new_ticket
                    if self._log_fn:
                        self._log_fn(
                            f"↩️  Undo: recreated cancelled SELL-STOP "
                            f"#{new_ticket} for [{state.name[:20]}]", "NEW")
        except Exception as e:
            log.warning("Undo: failed to recreate pending order: %s", e)

    def _recreate_pending(self, state, snap: Dict[str, Any], is_buy: bool) -> int:
        """Place a single pending order using the geometry the bot
        would normally compute, at the snapshot's lot size."""
        try:
            from core.order_manager import place_single_pending
            entry = state._buy_entry if is_buy else state._sell_entry
            sl    = state._buy_sl_price if is_buy else state._sell_sl_price
            tp    = state._buy_tp_price if is_buy else state._sell_tp_price
            lot   = snap["buy_lot"] if is_buy else snap["sell_lot"]
            return place_single_pending(
                symbol=state.symbol, is_buy=is_buy,
                entry=entry, sl=sl, tp=tp, lot=lot,
                comment="TB4_Undo"
            )
        except Exception as e:
            log.warning("_recreate_pending error: %s", e)
            return 0

    # ── Disk persistence ──────────────────────────────────────────

    def _save_to_disk(self):
        try:
            data = list(self.history)
            with open(_undo_file(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning("Failed to save undo history: %s", e)

    def _load_from_disk(self):
        try:
            path = _undo_file()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for snap in data[-UNDO_HISTORY_DEPTH:]:
                self.history.append(snap)
        except Exception as e:
            log.warning("Failed to load undo history: %s", e)

    def clear(self):
        with self._lock:
            self.history.clear()
            self._save_to_disk()


# ── Module-level singleton ────────────────────────────────────────
undo_manager = UndoManager()