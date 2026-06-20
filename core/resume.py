"""
resume.py — Bot State Recovery (TraderBot v4)
================================================
Saves session state to session_SYMBOL.json on every significant change.
On resume, loads exact state — no inference needed.

State file contains:
  - rect_top / rect_bottom (exact, fixed rectangle edges)
  - base_lot, soft_lot_mode
  - round number, touch_count
  - buy_lot, sell_lot
  - buy_pos_ticket, sell_pos_ticket
  - buy_ticket, sell_ticket (pending orders)
"""
import MetaTrader5 as mt5
import logging
import json
import os
import time as _time
from config import MAGIC_NUMBER
from core.order_manager import get_pip_size
from core.position_monitor import SourceState

log = logging.getLogger("resume")


def session_file(symbol: str) -> str:
    return f"session_{symbol}.json"


def save_session(state: SourceState):
    """Save current SourceState to disk. Called after every state change."""
    if state.name.startswith("RESUMED_") or not state.rect_top:
        return
    data = {
        "name":              state.name,
        "rect_top":          state.rect_top,
        "rect_bottom":       state.rect_bottom,
        "pip_size":          state.pip_size,
        "base_lot":          state.base_lot,
        "soft_lot_mode":     state.soft_lot_mode,
        "round":             state.round,
        "touch_count":       state.touch_count,
        "buy_lot":           state.buy_lot,
        "sell_lot":          state.sell_lot,
        "buy_ticket":        state.buy_ticket,
        "sell_ticket":       state.sell_ticket,
        "buy_pos_ticket":    state.buy_pos_ticket,
        "sell_pos_ticket":   state.sell_pos_ticket,
        "buy_confirmed":     state._buy_confirmed,
        "sell_confirmed":    state._sell_confirmed,
        "cumulative_loss":   state.cumulative_loss,
        "state":             state.state,
        "saved_at":          _time.time(),
    }
    try:
        with open(session_file(state.symbol), "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning("Could not save session: %s", e)


def clear_session(symbol: str):
    """Delete session file when sequence completes cleanly."""
    path = session_file(symbol)
    try:
        if os.path.exists(path):
            os.remove(path)
            log.info("Session file cleared")
    except Exception:
        pass


def scan_and_resume(symbol: str, pip_size: float, base_lot: float,
                    start_balance: float, risk_free_enabled: bool = False,
                    loss_free_enabled: bool = False, soft_lot_mode: int = 1,
                    log_fn=None, stop_fn=None) -> list:
    """
    Rebuild SourceState from saved session file + live MT5 data.
    Returns list of (name, SourceState) tuples.
    """
    _log = log_fn or (lambda msg, level="INFO": log.info(msg))

    # ── Try to load saved session file ────────────────────────────
    sf = session_file(symbol)
    data = None
    if os.path.exists(sf):
        try:
            with open(sf) as f:
                data = json.load(f)
        except Exception as e:
            _log(
                f"⚠️  Could not read session file: {e} — falling back to MT5 scan", "WARN")

    # ── Check what's actually live in MT5 ────────────────────────
    positions = mt5.positions_get(symbol=symbol) or []
    orders = mt5.orders_get(symbol=symbol) or []
    bot_pos = [p for p in positions if p.magic == MAGIC_NUMBER]
    bot_ord = [o for o in orders if o.magic == MAGIC_NUMBER]

    if not bot_pos and not bot_ord:
        _log("ℹ️  Resume: no open positions or orders found in MT5", "INFO")
        clear_session(symbol)
        return []

    _log(
        f"🔄  Resume: found {len(bot_pos)} position(s), "
        f"{len(bot_ord)} pending order(s)", "NEW"
    )

    # ── Reconstruct state ─────────────────────────────────────────
    if data and "rect_top" in data:
        # Use saved session data for exact values
        rtop = data["rect_top"]
        rbottom = data["rect_bottom"]
        p_size = data["pip_size"]
        b_lot = data["base_lot"]
        s_mode = data.get("soft_lot_mode", soft_lot_mode)
        rnd = data["round"]
        touches = data.get("touch_count", 0)
        buy_lot = data["buy_lot"]
        sell_lot = data["sell_lot"]
        cum_loss = data.get("cumulative_loss", 0.0)
        _log(
            f"📂  Loaded session file: rect=[{rbottom:.5f}-{rtop:.5f}] "
            f"R{rnd} touch={touches}", "INFO"
        )
    else:
        # Fallback: infer from MT5 data. Without a session file we
        # cannot recover the original rectangle edges exactly — best
        # effort is to derive them from whatever SL/entry prices are
        # still live (entry/SL pairs ARE the rectangle edges in v4).
        rtop, rbottom = _infer_rect(bot_pos, bot_ord)
        p_size = pip_size
        b_lot = base_lot
        s_mode = soft_lot_mode
        rnd = _infer_round(bot_pos, bot_ord, base_lot)
        touches = max(rnd - 1, 0)
        buy_lot, sell_lot = _infer_lots(bot_pos, bot_ord)
        cum_loss = 0.0
        _log(
            f"⚠️  No session file — inferred: rect=[{rbottom:.5f}-{rtop:.5f}] "
            f"R{rnd}", "WARN"
        )

    name = data["name"] if data else f"RESUMED_{int((rtop+rbottom)*50000)}"
    state = SourceState(
        name=name,
        rect_top=rtop,
        rect_bottom=rbottom,
        pip_size=p_size,
        symbol=symbol,
        base_lot=b_lot,
        start_balance=start_balance,
        log_fn=log_fn,
        stop_fn=stop_fn,
        risk_free_enabled=risk_free_enabled,
        loss_free_enabled=loss_free_enabled,
        soft_lot_mode=s_mode,
    )
    state.round = rnd
    state.touch_count = touches
    state.buy_lot = buy_lot
    state.sell_lot = sell_lot
    state.cumulative_loss = cum_loss

    # ── Map live MT5 objects to state ─────────────────────────────
    buy_pos = [p for p in bot_pos if p.type == 0]
    sell_pos = [p for p in bot_pos if p.type == 1]
    buy_ord = [o for o in bot_ord if o.type == mt5.ORDER_TYPE_BUY_STOP]
    sell_ord = [o for o in bot_ord if o.type == mt5.ORDER_TYPE_SELL_STOP]

    if buy_pos:
        pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
        state.buy_pos_ticket = pos.ticket
        state.buy_sl = pos.sl
        state.buy_r_frozen = abs(pos.price_open - pos.sl)
        state.buy_lot = pos.volume   # use actual volume from MT5
        state._buy_confirmed = True

    if sell_pos:
        pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
        state.sell_pos_ticket = pos.ticket
        state.sell_sl = pos.sl
        state.sell_r_frozen = abs(pos.price_open - pos.sl)
        state.sell_lot = pos.volume
        state._sell_confirmed = True

    if buy_ord:
        o = sorted(buy_ord, key=lambda o: o.time_setup, reverse=True)[0]
        state.buy_ticket = o.ticket
        if not buy_pos:  # no position, so pending lot is the BUY lot
            state.buy_lot = o.volume_current

    if sell_ord:
        o = sorted(sell_ord, key=lambda o: o.time_setup, reverse=True)[0]
        state.sell_ticket = o.ticket
        if not sell_pos:
            state.sell_lot = o.volume_current

    # ── Set state ─────────────────────────────────────────────────
    if bot_pos:
        state.state = SourceState.ACTIVE
        state._activated_at = _time.time() - 30
    elif bot_ord:
        state.state = SourceState.PENDING
    else:
        state.state = SourceState.IDLE

    # ── Log summary ───────────────────────────────────────────────
    parts = []
    if buy_pos:
        parts.append(
            f"BUY pos#{buy_pos[0].ticket} lot={buy_pos[0].volume:.2f}")
    if sell_pos:
        parts.append(
            f"SELL pos#{sell_pos[0].ticket} lot={sell_pos[0].volume:.2f}")
    if buy_ord:
        parts.append(
            f"BUY-STOP#{buy_ord[0].ticket} lot={buy_ord[0].volume_current:.2f}")
    if sell_ord:
        parts.append(
            f"SELL-STOP#{sell_ord[0].ticket} lot={sell_ord[0].volume_current:.2f}")

    _log(
        f"✅  Resumed: rect=[{rbottom:.5f}-{rtop:.5f}] R{state.round} | "
        + " | ".join(parts), "NEW"
    )
    return [(name, state)]


# ── Inference helpers (fallback when no session file) ─────────────

def _infer_rect(bot_pos, bot_ord):
    """
    Best-effort reconstruction of the rectangle's top/bottom edges
    from whatever entry/SL prices are still live. In v4 geometry,
    BUY's entry IS rect_top and SELL's entry IS rect_bottom (or vice
    versa, mirrored via SL) — so any live BUY price_open/SL or
    SELL price_open/SL pins one or both edges exactly.
    """
    top = bottom = None
    for p in bot_pos:
        if p.type == 0:  # BUY
            top = top or p.price_open
            bottom = bottom or p.sl
        else:  # SELL
            bottom = bottom or p.price_open
            top = top or p.sl
    for o in bot_ord:
        if o.type == mt5.ORDER_TYPE_BUY_STOP:
            top = top or o.price_open
            bottom = bottom or o.sl
        elif o.type == mt5.ORDER_TYPE_SELL_STOP:
            bottom = bottom or o.price_open
            top = top or o.sl
    if top is None or bottom is None or top == bottom:
        # Nothing usable — fall back to a degenerate zero-height
        # rectangle at whatever single price we found, rather than
        # crashing. This will behave oddly (zero R-distance) but
        # keeps resume from throwing; trader should verify on resume.
        any_price = top or bottom or 0.0
        return any_price, any_price
    return max(top, bottom), min(top, bottom)


def _infer_round(bot_pos, bot_ord, base_lot) -> int:
    all_lots = ([p.volume for p in bot_pos] +
                [o.volume_current for o in bot_ord])
    if not all_lots or base_lot <= 0:
        return 1
    # Soft-lot tables grow roughly linearly, not exponentially —
    # round ≈ (max_lot / base_lot), clamped to a sane range.
    try:
        return max(1, min(12, round(max(all_lots) / base_lot)))
    except Exception:
        return 1


def _infer_lots(bot_pos, bot_ord):
    buy_lot = next((p.volume for p in bot_pos if p.type == 0), None) or \
        next((o.volume_current for o in bot_ord if o.type ==
             mt5.ORDER_TYPE_BUY_STOP), 0.01)
    sell_lot = next((p.volume for p in bot_pos if p.type == 1), None) or \
        next((o.volume_current for o in bot_ord if o.type ==
             mt5.ORDER_TYPE_SELL_STOP), 0.01)
    return buy_lot, sell_lot
