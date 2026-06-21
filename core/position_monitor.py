"""
position_monitor.py — TraderBot v4

Rectangle-anchored. No round limit beyond MAX_TOUCHES (kill switch).
Runs until balance TP (R3) is hit, MAX_TOUCHES/hard-stop kill switch
fires, or the trader stops the bot.

LOGIC:
  Rectangle drawn: top edge = BUY-STOP entry, bottom edge = SELL-STOP
  entry. SL of each leg = exactly the opposite leg's entry (mirror
  geometry, unchanged from v3) — distance is simply the rectangle's
  height, there is no separate configured "distance" anymore.

  Rectangle touched (price enters/crosses either edge) → both legs
  placed at base ("start") lot.

  Each touch event (a leg activates and bumps its still-pending
  opposite, OR a leg closes via SL and a new recovery stop is
  placed) pulls the NEXT lot value from the configured soft-lot
  table (config.SOFT_LOT_TABLE_MODE1/2) instead of doubling. See
  _next_table_lot(). Touch count > config.MAX_TOUCHES trips the
  account-level kill switch instead of placing another order.

  R1 Loss-Free: floating profit ≥ LOSS_FREE_TRIGGER_R → SL → breakeven.
  R2 Risk-Free: floating profit ≥ RISK_FREE_TRIGGER_R → SL → locks
               cumulative_loss + this round's risk (same math as v3).
  R3 TP:        balance ≥ start_balance × BALANCE_TP_RATIO → close all
               & stop.

  Both R1 and R2's lock price are also drawn on the chart as thin,
  movable rectangles (TB4_R1_*/TB4_R2_*) — if the trader drags them,
  the watcher picks up the new price each scan and that overrides the
  calculated lock level (see SourceState.override_r1_price / r2_price).
"""
import MetaTrader5 as mt5
import logging
import time as _time
from config import MAGIC_NUMBER
from core.order_manager import send_pair, cancel_order, _filling_mode, _round_price
import config as cfg


def _save(state):
    """Save session state — imported lazily to avoid circular import."""
    try:
        from core.resume import save_session
        save_session(state)
    except Exception:
        pass

log = logging.getLogger("monitor_v2")

ACTIVATION_GRACE_SEC = 5


class SourceState:
    IDLE      = "idle"
    PENDING   = "pending"
    ACTIVE    = "active"
    EXHAUSTED = "exhausted"

    def __init__(self, name, rect_top, rect_bottom, pip_size, symbol, base_lot,
                 start_balance=0.0, log_fn=None, stop_fn=None, kill_fn=None,
                 risk_free_enabled=False, loss_free_enabled=False,
                 soft_lot_mode=1):
        self.name          = name
        # Fixed rectangle edges — NEVER move after registration. This
        # is the "distance" now: rect_top - rect_bottom. (item 5/11)
        self.rect_top      = max(rect_top, rect_bottom)
        self.rect_bottom   = min(rect_top, rect_bottom)
        self.pip_size      = pip_size
        self.symbol        = symbol
        self.base_lot      = base_lot
        self.start_balance = start_balance
        self._log          = log_fn or (lambda msg, level="INFO": log.info(msg))
        self._stop_fn      = stop_fn
        self._kill_fn      = kill_fn or stop_fn
        self._risk_free_enabled = risk_free_enabled
        self._loss_free_enabled = loss_free_enabled
        self.soft_lot_mode      = soft_lot_mode if soft_lot_mode in (1, 2, 3) else 1

        self.state           = self.IDLE
        self.round           = 0

        self.buy_ticket      = None
        self.sell_ticket     = None
        self.buy_pos_ticket  = None
        self.sell_pos_ticket = None

        self.buy_lot         = base_lot
        self.sell_lot        = base_lot
        self.buy_sl          = None
        self.sell_sl         = None
        self.buy_r_frozen    = 0.0
        self.sell_r_frozen   = 0.0

        self._buy_confirmed  = False
        self._sell_confirmed = False
        self._activated_at   = 0.0
        self._last_bid       = 0.0
        self._last_ask       = 0.0

        self.registered_at   = 0
        self.last_prev_t     = 0

        # ── Soft lot table (item 4) ───────────────────────────────
        # touch_count starts at 0 ("start" lot, both legs). Every
        # bump event (see _next_table_lot) increments it by 1 and
        # pulls the next value from the configured table. Exceeding
        # config.MAX_TOUCHES trips the kill switch.
        self.touch_count = 0

        # ── R1 (Loss-Free) / R2 (Risk-Free) mechanism ─────────────
        # R1: floating profit ≥ LOSS_FREE_TRIGGER_R → SL → breakeven.
        # R2: floating profit ≥ RISK_FREE_TRIGGER_R → SL → locks
        #     cumulative_loss + this round's risk (same math as v3's
        #     single risk-free mechanism, now numbered R2).
        # Either firing for a side, then that side later closing for
        # ANY reason, triggers a full reset back to IDLE — the
        # opposite pending stop is left untouched. (unchanged from v3)
        self.loss_free_applied = {"buy": False, "sell": False}
        self.risk_free_applied = {"buy": False, "sell": False}
        self.needs_full_reset  = False   # watcher checks/clears this

        # Trader-adjustable override for the R1/R2 chart lines (item 9).
        # None = use the calculated price. If the trader drags the
        # bot-drawn TB4_R1_*/TB4_R2_* rectangle, the watcher writes
        # the new price here each scan, and _check_loss_free /
        # _check_risk_free use it instead of recalculating.
        self.override_r1_price = {"buy": None, "sell": None}
        self.override_r2_price = {"buy": None, "sell": None}

        # ── Cumulative loss tracking (for TP sizing) ──────────────
        # Tracks total dollar loss across all closed losing positions
        # in this cycle. Reset to 0 on a full reset (win or R1/R2
        # close). Used by _tp_pips to set TP based on actual loss
        # accumulated, not a theoretical estimate.
        self.cumulative_loss = 0.0
        self._pip_value_per_base_lot = 0.0  # calibrated from real SL closes

        # ── Tick-based touch detection (timeframe-immune) ─────────
        self._prev_tick_price = None   # last seen mid price, for crossing detection

        self._log(
            f"⚙️  [{self.name[:20]}] mode={self.soft_lot_mode} "
            f"loss_free={self._loss_free_enabled} risk_free={self._risk_free_enabled} "
            f"rect=[{self.rect_bottom:.5f}-{self.rect_top:.5f}]",
            "INFO"
        )

    # ── Fixed price properties (never drift) ──────────────────────
    @property
    def _dist(self):
        """Half the rectangle height — kept as a property purely so
        every downstream formula that historically read self._dist
        (TP sizing, R distance, pip calibration) keeps working
        unchanged. Distance is now derived from the rectangle, not a
        configured pip value."""
        return (self.rect_top - self.rect_bottom) / 2.0

    def _next_table_lot(self, base_lot: float = None) -> float:
        """
        Advance touch_count by 1 and return the lot for that touch.

        Modes 1/2: looks up the next value from the configured
        soft-lot table. If this would exceed config.MAX_TOUCHES,
        trips the kill switch instead and returns None — every call
        site MUST check for None and bail out (no order placement)
        when it gets None back.

        Mode 3 (classic martingale): NOT table-driven. Returns
        round(base_lot * 2, 2), floored at 0.01 — the exact original
        doubling formula, applied to whatever lot the caller passes
        as `base_lot` (the leg this new lot is doubling off of — see
        each call site for which lot that is, matching the original
        code exactly). No MAX_TOUCHES cap in this mode: touch_count
        still increments (for logging/GUI), but growth is bounded
        only by the deep-round OB+FVG bounce-confluence gate (lot
        >= 0.64) and margin protection, exactly like the original
        behavior this mode restores.
        """
        if self.soft_lot_mode == 3:
            self.touch_count += 1
            src = base_lot if base_lot is not None else self.base_lot
            return max(round(src * 2, 2), 0.01)

        table = (cfg.SOFT_LOT_TABLE_MODE1 if self.soft_lot_mode == 1
                 else cfg.SOFT_LOT_TABLE_MODE2)
        next_touch = self.touch_count + 1
        if next_touch > getattr(cfg, "MAX_TOUCHES", 11):
            self._log(
                f"💀  [{self.name[:20]}] MAX_TOUCHES "
                f"({getattr(cfg, 'MAX_TOUCHES', 11)}) exceeded — "
                f"tripping kill switch (closing everything & halting)", "ERROR"
            )
            self._close_all_and_stop()
            return None
        self.touch_count = next_touch
        idx = min(self.touch_count, len(table) - 1)
        return table[idx]

    def _current_spread(self) -> float:
        """
        Current bid-ask spread in price units, fetched live from MT5.
        Used to compensate order entry prices so the EFFECTIVE fill
        price (what MT5 actually executes at) matches the intended
        level exactly, regardless of current spread width.

        BUY_STOP fills at ask: to get a fill at `intended`, place the
        stop at `intended − spread` so that when bid reaches
        (intended − spread), ask = intended and the fill is exact.

        SELL_STOP fills at bid: to get a fill at `intended`, place the
        stop at `intended + spread` so that when ask reaches
        (intended + spread), bid = intended and the fill is exact.

        Returns 0.0 on failure (no compensation applied, safe fallback).
        """
        try:
            tick = mt5.symbol_info_tick(self.symbol)
            if tick and tick.ask > 0 and tick.bid > 0:
                return round(tick.ask - tick.bid, 5)
        except Exception:
            pass
        return 0.0

    @property
    def _buy_entry(self):
        """BUY_STOP entry price = the rectangle's TOP edge,
        spread-compensated so the effective MT5 fill lands exactly
        at rect_top regardless of spread."""
        raw    = _round_price(self.rect_top, self.symbol)
        spread = self._current_spread()
        return _round_price(raw - spread, self.symbol)

    @property
    def _sell_entry(self):
        """SELL_STOP entry price = the rectangle's BOTTOM edge,
        spread-compensated so the effective MT5 fill lands exactly
        at rect_bottom regardless of spread."""
        raw    = _round_price(self.rect_bottom, self.symbol)
        spread = self._current_spread()
        return _round_price(raw + spread, self.symbol)

    @property
    def _buy_sl_price(self):
        """
        SL of BUY = EXACTLY the SELL side's real entry price
        (_sell_entry, including its spread compensation) — not the
        theoretical pre-spread line position. This is what makes BUY's
        SL land exactly where SELL actually fills, with zero gap,
        matching the bot's whole "zero spread" design intent: if the
        SELL position is sitting at 4134.97, BUY's SL must be 4134.97,
        not some epsilon above or below it.
        """
        return self._sell_entry

    @property
    def _sell_sl_price(self):
        """SL of SELL = EXACTLY the BUY side's real entry price
        (_buy_entry). See _buy_sl_price — same reasoning, mirrored."""
        return self._buy_entry

    def _reanchor_buy(self, close_price: float):
        """
        No-op in the base class. rect_top/rect_bottom are the fixed
        anchors and must NEVER move — every new recovery order keeps
        using the same rectangle edges, and BUY/SELL SL mirroring
        (_buy_sl_price == _sell_entry and vice versa) is already
        exact by construction since both sides derive from these two
        shared, unmoving edges. There is no slippage gap to correct
        here: SL price and the opposite side's entry price are
        literally the same computed value, not two independently-
        sourced numbers that could drift apart.
        """
        pass

    def _reanchor_sell(self, close_price: float):
        """No-op in the base class. See _reanchor_buy."""
        pass

    def _dollar_per_pip(self, lot: float) -> float:
        """
        Dollar profit per 1 pip per given lot size, in account currency.
        Primary: mt5.order_calc_profit() — broker's own engine.
        Fallback: derive from trade_tick_value / trade_tick_size.
        Emergency fallback: use self._pip_value_cache if we already
        calibrated from a real closed position (see _calibrate_pip_value).
        Returns 0.0 only if everything fails — caller must guard.
        """
        try:
            tick = mt5.symbol_info_tick(self.symbol)
            price = (tick.bid + tick.ask) / 2.0 if tick else (self.rect_top + self.rect_bottom) / 2.0
            profit = mt5.order_calc_profit(
                mt5.ORDER_TYPE_BUY, self.symbol, lot, price, price + self.pip_size
            )
            if profit is not None and profit > 0:
                return float(profit)
        except Exception:
            pass

        try:
            info = mt5.symbol_info(self.symbol)
            if info and info.trade_tick_size > 0:
                return (info.trade_tick_value / info.trade_tick_size) * self.pip_size * lot
        except Exception:
            pass

        # Last resort: use cached value from a real closed SL, normalised to lot
        if getattr(self, '_pip_value_per_base_lot', 0.0) > 0:
            return self._pip_value_per_base_lot * lot
        return 0.0

    def _calibrate_pip_value(self, closed_dollar_loss: float, closed_lot: float):
        """
        Back-calculate dollar-per-pip-per-base-lot from a real SL close.
        SL distance = the full rectangle height (top to bottom) in
        pips (item 5/11 — the rectangle height IS the distance now;
        entry to SL is exactly the opposite edge).
        Stored in _pip_value_per_base_lot so _dollar_per_pip can use it
        as a reliable fallback even when order_calc_profit returns None.
        """
        sl_pips = (self.rect_top - self.rect_bottom) / self.pip_size
        if sl_pips > 0 and closed_lot > 0 and closed_dollar_loss > 0:
            self._pip_value_per_base_lot = (
                closed_dollar_loss / sl_pips / closed_lot * self.base_lot
            )
            self._log(
                f"📐  [{self.name[:20]}] pip value calibrated from real SL: "
                f"${self._pip_value_per_base_lot:.4f}/pip/base_lot "
                f"(loss=${closed_dollar_loss:.2f} lot={closed_lot:.2f})", "INFO"
            )

    @property
    def _tp_pips(self) -> float:
        """
        TP distance in pips, sized to cover ALL losses accumulated so
        far in this cycle plus an equal profit on top — minimum 1:2
        RR (cover loss + win the same again), targeting 1:3 (cover
        loss + win 2× the total loss).

        Formula: tp_pips = (cumulative_loss + current_sl_risk) × RR
                           / dollar_per_pip(current_lot)

        Hard floor: rectangle height (pips) × 3 — was dist_pips × 3
        in v3; the rectangle height is the equivalent quantity now
        that there's no separate configured distance (item 5/11).
        Hard ceiling: none — we let the RR math determine the
        distance, since capping it is exactly what caused the TP to
        never move.
        """
        current_lot = max(self.buy_lot, self.sell_lot, self.base_lot)
        dpp = self._dollar_per_pip(current_lot)

        rect_pips  = (self.rect_top - self.rect_bottom) / self.pip_size
        floor_pips = rect_pips * 3

        if dpp <= 0:
            self._log(
                f"⚠️  [{self.name[:20]}] _dollar_per_pip returned 0 "
                f"(order_calc_profit unavailable?) — using floor {floor_pips}p",
                "WARN"
            )
            return floor_pips

        # Current round's SL risk in dollars — full rectangle height,
        # since entry-to-SL on EITHER leg spans top-to-bottom exactly
        # once (not 2× — that was line+dist*2 geometry, this is the
        # rectangle's own height).
        sl_pips = rect_pips
        current_sl_risk = dpp * sl_pips  # if this position's SL is hit next

        total_at_risk = self.cumulative_loss + current_sl_risk

        # Try 1:3 first; if it puts TP unreasonably far (>200p) fall
        # back to 1:2. This preserves the cycle's ability to actually
        # recover even on deep runs.
        for rr in (3, 2):
            tp_pips = (total_at_risk * rr) / dpp
            if tp_pips <= 200.0:
                return max(tp_pips, floor_pips)

        # Even 1:2 is > 200 pips — use 200p ceiling but never below floor
        return max(200.0, floor_pips)

    @property
    def _buy_tp_price(self):
        tp_dist = self._tp_pips * self.pip_size
        return _round_price(self._buy_entry + tp_dist, self.symbol)

    @property
    def _sell_tp_price(self):
        tp_dist = self._tp_pips * self.pip_size
        return _round_price(self._sell_entry - tp_dist, self.symbol)

    @property
    def _buy_r_distance(self) -> float:
        """
        1R for the BUY side = the fixed structural distance from
        BUY's entry (rect_top) to BUY's SL (rect_bottom) = the full
        rectangle height, in price units.

        IMPORTANT: this must NOT be computed from _buy_entry/_buy_sl_price
        directly — _buy_entry now includes live spread compensation
        (see _buy_entry), which fluctuates tick-to-tick. Using it here
        would make R a moving target instead of the fixed risk size
        the position was actually opened with. R is always exactly
        the rectangle height (rect_top - rect_bottom) for the base
        class's symmetric mirror geometry.
        """
        return abs(self.rect_top - self.rect_bottom)

    @property
    def _sell_r_distance(self) -> float:
        """1R for the SELL side. See _buy_r_distance — same reasoning,
        same fixed value (rectangle height) for the base class."""
        return abs(self.rect_top - self.rect_bottom)

    # ── Public API ────────────────────────────────────────────────

    def check_touch(self, bid: float, ask: float) -> bool:
        """
        Tick-based touch detection — timeframe-immune.

        Returns True (and transitions to PENDING via place_initial_pair)
        if price has touched either edge of the rectangle since the
        last tick we saw.

        Detects a touch two ways, against EITHER edge (rect_top or
        rect_bottom):
          1. Direct straddle: bid <= edge <= ask right now
          2. Crossing: the mid price moved from one side of an edge
             to the other between the previous tick and this one
             (catches fast moves where price jumps over an edge
             between two ticks without ever exactly straddling it)

        This does not depend on any EA-reported candle data, so it
        is unaffected by the user switching the MT5 chart's displayed
        timeframe mid-session.
        """
        if self.state != self.IDLE:
            return False
        if bid <= 0 or ask <= 0:
            return False

        mid = (bid + ask) / 2

        touched = False
        desc    = ""

        for edge_name, edge in (("top", self.rect_top), ("bottom", self.rect_bottom)):
            if bid <= edge <= ask:
                touched = True
                desc    = f"{edge_name} bid/ask straddle bid={bid:.5f} ask={ask:.5f}"
                break
            if self._prev_tick_price is not None:
                prev = self._prev_tick_price
                if (prev < edge <= mid) or (mid <= edge < prev):
                    touched = True
                    desc    = f"{edge_name} crossed {prev:.5f}→{mid:.5f}"
                    break

        self._prev_tick_price = mid

        if touched:
            self._log(
                f"🎯  [{self.name[:20]}] touched ({desc}) | "
                f"rect=[{self.rect_bottom:.5f}-{self.rect_top:.5f}] | placing orders", "NEW"
            )
            self.place_initial_pair()
            return True

        return False

    def place_initial_pair(self):
        self.round       = 1
        self.touch_count = 0
        self.buy_lot  = self.base_lot
        self.sell_lot = self.base_lot

        orders = [
            {"type": "BUY_STOP",  "entry": self._buy_entry,  "sl": self._buy_sl_price,
             "tp": self._buy_tp_price, "lot": self.buy_lot,  "source": self.rect_top, "round": 1},
            {"type": "SELL_STOP", "entry": self._sell_entry, "sl": self._sell_sl_price,
             "tp": self._sell_tp_price, "lot": self.sell_lot, "source": self.rect_bottom, "round": 1},
        ]
        results = send_pair(orders, self.symbol)

        self.buy_ticket  = None
        self.sell_ticket = None
        for r in results:
            if r["ok"]:
                if r["order"]["type"] == "BUY_STOP":
                    self.buy_ticket = r["ticket"]
                    self.buy_sl     = self._buy_sl_price
                else:
                    self.sell_ticket = r["ticket"]
                    self.sell_sl     = self._sell_sl_price

        # ── Both legs placed: normal success path ──────────────────
        if self.buy_ticket and self.sell_ticket:
            self.state = self.PENDING
            self._log(
                f"📌  [{self.name[:20]}] R1 pair placed | "
                f"BUY#{self.buy_ticket}@{self._buy_entry:.5f} "
                f"sl={self._buy_sl_price:.5f} lot={self.buy_lot:.2f} | "
                f"SELL#{self.sell_ticket}@{self._sell_entry:.5f} "
                f"sl={self._sell_sl_price:.5f} lot={self.sell_lot:.2f}", "NEW"
            )
            return

        # ── Neither leg placed: clean failure, stay IDLE ───────────
        if not self.buy_ticket and not self.sell_ticket:
            self._log(f"❌  [{self.name[:20]}] failed to place initial pair "
                      f"(both legs failed)", "ERROR")
            return

        # ── Exactly ONE leg placed: retry the missing leg a few times
        # before giving up. A single-leg "pair" defeats the whole
        # martingale design (no opposite hedge to activate on
        # recovery), so this must not be silently treated as success.
        missing_side = "SELL_STOP" if self.buy_ticket else "BUY_STOP"
        self._log(
            f"⚠️  [{self.name[:20]}] {missing_side} failed to place — "
            f"retrying ({'BUY' if self.buy_ticket else 'SELL'} leg already "
            f"placed)", "WARN"
        )

        MAX_RETRIES = 3
        got_missing_leg = False
        for attempt in range(1, MAX_RETRIES + 1):
            _time.sleep(1.0)
            if missing_side == "SELL_STOP":
                missing_order = {"type": "SELL_STOP", "entry": self._sell_entry,
                                 "sl": self._sell_sl_price, "tp": self._sell_tp_price,
                                 "lot": self.sell_lot, "source": self.rect_bottom, "round": 1}
            else:
                missing_order = {"type": "BUY_STOP", "entry": self._buy_entry,
                                 "sl": self._buy_sl_price, "tp": self._buy_tp_price,
                                 "lot": self.buy_lot, "source": self.rect_top, "round": 1}

            retry_results = send_pair([missing_order], self.symbol)
            ok = [r for r in retry_results if r["ok"]]
            if ok:
                if missing_side == "SELL_STOP":
                    self.sell_ticket = ok[0]["ticket"]
                    self.sell_sl     = self._sell_sl_price
                else:
                    self.buy_ticket = ok[0]["ticket"]
                    self.buy_sl     = self._buy_sl_price
                got_missing_leg = True
                self._log(
                    f"✅  [{self.name[:20]}] {missing_side} retry succeeded "
                    f"(attempt {attempt}/{MAX_RETRIES})", "NEW"
                )
                break
            self._log(
                f"⚠️  [{self.name[:20]}] {missing_side} retry "
                f"{attempt}/{MAX_RETRIES} failed", "WARN"
            )

        if got_missing_leg:
            self.state = self.PENDING
            self._log(
                f"📌  [{self.name[:20]}] R1 pair placed (after retry) | "
                f"BUY#{self.buy_ticket}@{self._buy_entry:.5f} "
                f"sl={self._buy_sl_price:.5f} lot={self.buy_lot:.2f} | "
                f"SELL#{self.sell_ticket}@{self._sell_entry:.5f} "
                f"sl={self._sell_sl_price:.5f} lot={self.sell_lot:.2f}", "NEW"
            )
            return

        # ── Retries exhausted: cancel the lone leg and reset cleanly,
        # rather than running with only half a pair (no hedge at all).
        self._log(
            f"❌  [{self.name[:20]}] {missing_side} could not be placed "
            f"after {MAX_RETRIES} retries — cancelling lone leg and "
            f"resetting to IDLE", "ERROR"
        )
        if self.buy_ticket:
            cancel_order(self.buy_ticket)
        if self.sell_ticket:
            cancel_order(self.sell_ticket)
        self.reset()

    def check(self, candle: dict):
        bid = candle.get("BID", 0.0)
        if bid:
            self._last_bid = bid
        tick = mt5.symbol_info_tick(self.symbol)
        if tick:
            self._last_bid = tick.bid
            self._last_ask = tick.ask

        if self.state in (self.IDLE, self.EXHAUSTED):
            return

        self._check_balance_tp()
        self._check_hard_stop_loss()

        if self.state == self.PENDING:
            self._check_activation()
        elif self.state == self.ACTIVE:
            self._check_legs()

    # ── Balance TP (R3) ──────────────────────────────────────────────

    def _check_balance_tp(self):
        if self.start_balance <= 0:
            return
        try:
            ratio  = getattr(cfg, 'BALANCE_TP_RATIO', 1.10)
            info   = mt5.account_info()
            if not info:
                return
            target = self.start_balance * ratio
            if info.balance >= target:
                self._log(
                    f"🎯  [{self.name[:20]}] Balance TP! "
                    f"{info.balance:.2f} ≥ {target:.2f} — closing all & stopping", "NEW"
                )
                self._close_all_and_stop()
        except Exception as e:
            log.warning("Balance TP check error: %s", e)

    # ── Hard kill switch (item 1 / queued feature #1) ────────────────
    # Account-level circuit breaker, independent of MAX_TOUCHES. If
    # equity drops to start_balance*(1-HARD_STOP_LOSS_RATIO), close
    # everything for this symbol and halt the bot completely.
    def _check_hard_stop_loss(self):
        if self.start_balance <= 0:
            return
        try:
            ratio = getattr(cfg, 'HARD_STOP_LOSS_RATIO', 0.50)
            info  = mt5.account_info()
            if not info:
                return
            floor = self.start_balance * (1.0 - ratio)
            if info.equity <= floor:
                self._log(
                    f"💀  [{self.name[:20]}] HARD STOP-LOSS! equity "
                    f"{info.equity:.2f} ≤ floor {floor:.2f} "
                    f"({ratio*100:.0f}% of start {self.start_balance:.2f}) — "
                    f"closing all & halting bot", "ERROR"
                )
                self._close_all_and_stop()
        except Exception as e:
            log.warning("Hard stop-loss check error: %s", e)

    def _close_all_and_stop(self):
        filling = _filling_mode(self.symbol)
        tick    = mt5.symbol_info_tick(self.symbol)

        # Close all open positions
        for p in (mt5.positions_get(symbol=self.symbol) or []):
            if p.magic != MAGIC_NUMBER:
                continue
            is_buy = p.type == 0
            res = mt5.order_send({
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       self.symbol,
                "volume":       p.volume,
                "type":         mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "position":     p.ticket,
                "price":        tick.bid if is_buy else tick.ask,
                "deviation":    30,
                "magic":        MAGIC_NUMBER,
                "comment":      "TB2_BalTP",
                "type_filling": filling,
            })
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._log(f"✅  Closed #{p.ticket}", "NEW")
            else:
                self._log(f"⚠️  Failed to close #{p.ticket}", "WARN")

        # Cancel all pending orders
        for o in (mt5.orders_get(symbol=self.symbol) or []):
            if o.magic == MAGIC_NUMBER:
                cancel_order(o.ticket)

        # Delete saved start balance so next session starts fresh
        import os as _os
        _bal_file = f"start_balance_{self.symbol}.json"
        try:
            if _os.path.exists(_bal_file):
                _os.remove(_bal_file)
                self._log(f"🗑️  Cleared saved start balance (session complete)", "INFO")
        except Exception:
            pass

        self.state = self.EXHAUSTED

        # Signal the watcher to stop cleanly.
        # The watcher's _on_balance_tp() sets its stop event and emits
        # sig.emit_stop() so the GUI can stop FVG/OB/Confluence watchers
        # before mt5.shutdown() is called at the end of watcher.run().
        # DO NOT call mt5.shutdown() here — the connection must stay alive
        # until the watcher loop exits naturally.
        if self._stop_fn:
            self._stop_fn()

    # ── Activation ────────────────────────────────────────────────

    def _check_activation(self):
        pending   = {o.ticket for o in (mt5.orders_get(symbol=self.symbol) or [])
                     if o.magic == MAGIC_NUMBER}
        positions = mt5.positions_get(symbol=self.symbol) or []
        bot_pos   = [p for p in positions if p.magic == MAGIC_NUMBER]
        buy_pos   = [p for p in bot_pos if p.type == 0]
        sell_pos  = [p for p in bot_pos if p.type == 1]

        buy_still  = self.buy_ticket  in pending if self.buy_ticket  else False
        sell_still = self.sell_ticket in pending if self.sell_ticket else False
        buy_filled  = self.buy_ticket  is not None and not buy_still
        sell_filled = self.sell_ticket is not None and not sell_still

        if not buy_filled and not sell_filled:
            return

        self._activated_at   = _time.time()
        self._buy_confirmed  = False
        self._sell_confirmed = False

        if buy_filled:
            if buy_pos:
                pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
                self.buy_pos_ticket = pos.ticket
                self.buy_sl         = pos.sl
                self.buy_r_frozen   = abs(pos.price_open - pos.sl)
                self.buy_lot        = pos.volume
                self._buy_confirmed = True
            self.buy_ticket = None

        if sell_filled:
            if sell_pos:
                pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
                self.sell_pos_ticket = pos.ticket
                self.sell_sl         = pos.sl
                self.sell_r_frozen   = abs(pos.price_open - pos.sl)
                self.sell_lot        = pos.volume
                self._sell_confirmed = True
            self.sell_ticket = None

        if buy_filled and not sell_filled:
            next_lot = self._next_table_lot(base_lot=self.buy_lot)
            if next_lot is None:
                return  # kill switch tripped
            self.sell_lot = next_lot
            if self.sell_ticket:
                self._modify_order_lot(self.sell_ticket, self.sell_lot,
                                       exact_sl=self._sell_sl_price)
            self._log(
                f"🟢  [{self.name[:20]}] R{self.round} BUY activated | "
                f"pos#{self.buy_pos_ticket} sl={self._buy_sl_price:.5f} | "
                f"SELL#{self.sell_ticket} lot (touch {self.touch_count}) → {self.sell_lot:.2f}", "NEW"
            )
        elif sell_filled and not buy_filled:
            next_lot = self._next_table_lot(base_lot=self.sell_lot)
            if next_lot is None:
                return  # kill switch tripped
            self.buy_lot = next_lot
            if self.buy_ticket:
                if self.buy_ticket in pending:
                    self._modify_order_lot(self.buy_ticket, self.buy_lot,
                                           exact_sl=self._buy_sl_price)
                else:
                    self.buy_lot = buy_pos[0].volume if buy_pos else self.buy_lot
            self._log(
                f"🔴  [{self.name[:20]}] R{self.round} SELL activated | "
                f"pos#{self.sell_pos_ticket} sl={self._sell_sl_price:.5f} | "
                f"BUY#{self.buy_ticket} lot (touch {self.touch_count}) → {self.buy_lot:.2f}", "NEW"
            )
        else:
            self._log(
                f"🟢🔴  [{self.name[:20]}] R{self.round} BOTH activated | "
                f"BUY#{self.buy_pos_ticket} SELL#{self.sell_pos_ticket}", "NEW"
            )

        self.state = self.ACTIVE
        _save(self)

    # ── Active leg monitoring ─────────────────────────────────────

    def _check_legs(self):
        now      = _time.time()
        in_grace = (now - self._activated_at) < ACTIVATION_GRACE_SEC

        positions    = mt5.positions_get(symbol=self.symbol) or []
        bot_pos      = [p for p in positions if p.magic == MAGIC_NUMBER]
        open_tickets = {p.ticket for p in bot_pos}
        buy_pos      = [p for p in bot_pos if p.type == 0]
        sell_pos     = [p for p in bot_pos if p.type == 1]

        pending = {o.ticket for o in (mt5.orders_get(symbol=self.symbol) or [])
                   if o.magic == MAGIC_NUMBER}

        # ── Confirm unconfirmed positions ─────────────────────────
        if not self._buy_confirmed and self.buy_pos_ticket is None:
            if buy_pos:
                pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
                self.buy_pos_ticket = pos.ticket
                self.buy_sl         = pos.sl
                self.buy_r_frozen   = abs(pos.price_open - pos.sl)
                self.buy_lot        = pos.volume
                self._buy_confirmed = True
                self._log(
                    f"🔍  [{self.name[:20]}] BUY pos confirmed "
                    f"#{pos.ticket} sl={pos.sl}", "INFO"
                )

        if not self._sell_confirmed and self.sell_pos_ticket is None:
            if sell_pos:
                pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
                self.sell_pos_ticket = pos.ticket
                self.sell_sl         = pos.sl
                self.sell_r_frozen   = abs(pos.price_open - pos.sl)
                self.sell_lot        = pos.volume
                self._sell_confirmed = True
                self._log(
                    f"🔍  [{self.name[:20]}] SELL pos confirmed "
                    f"#{pos.ticket} sl={pos.sl}", "INFO"
                )

        # ── Second activation (pending stop filled) ───────────────
        if (self.buy_ticket and self.buy_ticket not in pending
                and not self._buy_confirmed):
            if buy_pos:
                pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
                self.buy_pos_ticket = pos.ticket
                self.buy_sl         = pos.sl
                self.buy_r_frozen   = abs(pos.price_open - pos.sl)
                self.buy_lot        = pos.volume
                self._buy_confirmed = True
                self.buy_ticket     = None
                next_lot = self._next_table_lot(base_lot=self.buy_lot)
                if next_lot is None:
                    return  # kill switch tripped
                self.sell_lot = next_lot
                if self.sell_ticket and self.sell_ticket in pending:
                    self._modify_order_lot(self.sell_ticket, self.sell_lot,
                                           exact_sl=self._sell_sl_price)
                self.round += 1
                self._log(
                    f"🟢  [{self.name[:20]}] R{self.round} BUY activated (2nd) | "
                    f"pos#{self.buy_pos_ticket} | SELL lot (touch {self.touch_count}) → {self.sell_lot:.2f}", "NEW"
                )
            else:
                self.buy_ticket = None

        if (self.sell_ticket and self.sell_ticket not in pending
                and not self._sell_confirmed):
            if sell_pos:
                pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
                self.sell_pos_ticket = pos.ticket
                self.sell_sl         = pos.sl
                self.sell_r_frozen   = abs(pos.price_open - pos.sl)
                self.sell_lot        = pos.volume
                self._sell_confirmed = True
                self.sell_ticket     = None
                next_lot = self._next_table_lot(base_lot=self.sell_lot)
                if next_lot is None:
                    return  # kill switch tripped
                self.buy_lot = next_lot
                if self.buy_ticket and self.buy_ticket in pending:
                    self._modify_order_lot(self.buy_ticket, self.buy_lot,
                                           exact_sl=self._buy_sl_price)
                self.round += 1
                self._log(
                    f"🔴  [{self.name[:20]}] R{self.round} SELL activated (2nd) | "
                    f"pos#{self.sell_pos_ticket} | BUY lot (touch {self.touch_count}) → {self.buy_lot:.2f}", "NEW"
                )
            else:
                self.sell_ticket = None

        if in_grace:
            return

        # ── Keep each open position's TP synced to the balance-target
        # gap as it shrinks/grows (lot changes, balance moves from a
        # sibling line, etc.) — see _resync_open_tp.
        self._resync_open_tp(buy_pos, sell_pos)
        self._resync_open_sl(buy_pos, sell_pos)

        # ── R1 Loss-Free: move SL to breakeven once floating profit ≥1R ─
        self._check_loss_free(buy_pos, sell_pos)

        # ── R2 Risk-Free: move SL to lock cumulative_loss+risk once ≥2R ─
        self._check_risk_free(buy_pos, sell_pos)

        # ── Detect closed positions → place new stop ──────────────
        # Pass the EXACT price the closed position's SL executed at,
        # so the new same-side pending order anchors to that real
        # fill price instead of recalculating from the original fixed
        # line/zone anchor. Slippage on the SL fill would otherwise
        # leave a small gap between where the position actually closed
        # and where the new order sits, adding delay before it can
        # trigger again.
        if (self.buy_pos_ticket
                and self.buy_pos_ticket not in open_tickets
                and self._buy_confirmed):
            closed_ticket = self.buy_pos_ticket
            close_price, close_reason = self._get_close_info(closed_ticket)
            was_risk_free = self.risk_free_applied.get("buy", False)
            was_loss_free = self.loss_free_applied.get("buy", False)
            self._log(
                f"📉  [{self.name[:20]}] BUY pos#{closed_ticket} closed"
                + (f" @ {close_price:.5f}" if close_price else "")
                + (f" ({close_reason})" if close_reason else "")
                + (" (was risk-free)" if was_risk_free else "")
                + (" (was loss-free)" if was_loss_free and not was_risk_free else ""), "WARN"
            )
            self.buy_pos_ticket = None
            self._buy_confirmed = False
            if was_risk_free or was_loss_free:
                # R1/R2 already locked this slot's outcome — this
                # source is done for this cycle. Cancel the still-
                # pending opposite stop (handled by reset()), mark
                # for a full reset (chart object cleanup is the
                # watcher's job), and do NOT start a new recovery
                # cycle. (No auto-relocate in v4 — item 2 — the source
                # simply goes IDLE and waits for the next real touch
                # of its own rectangle, or removal if the trader
                # deletes/redraws it.)
                tag = "risk-free" if was_risk_free else "loss-free"
                self._log(
                    f"🟢  [{self.name[:20]}] {tag} BUY closed — "
                    f"resetting to IDLE", "NEW"
                )
                self.needs_full_reset = True
                self.reset()
            elif close_reason == "tp":
                # Round WON outright via take-profit — the cycle is
                # complete and successful. Do NOT chain into another
                # recovery order at a bigger lot.
                self._log(
                    f"🏆  [{self.name[:20]}] BUY hit TP — round won, "
                    f"resetting to IDLE", "NEW"
                )
                self.needs_full_reset = True
                self.reset()
            else:
                # SL hit (or unknown) — accumulate real loss and
                # calibrate pip value from the real close, then recover.
                real_loss = self._get_real_loss(closed_ticket)
                if real_loss > 0:
                    self.cumulative_loss += real_loss
                    self._calibrate_pip_value(real_loss, self.buy_lot)
                self._place_new_buy_stop(anchor_price=close_price)

        if (self.sell_pos_ticket
                and self.sell_pos_ticket not in open_tickets
                and self._sell_confirmed):
            closed_ticket = self.sell_pos_ticket
            close_price, close_reason = self._get_close_info(closed_ticket)
            was_risk_free = self.risk_free_applied.get("sell", False)
            was_loss_free = self.loss_free_applied.get("sell", False)
            self._log(
                f"📉  [{self.name[:20]}] SELL pos#{closed_ticket} closed"
                + (f" @ {close_price:.5f}" if close_price else "")
                + (f" ({close_reason})" if close_reason else "")
                + (" (was risk-free)" if was_risk_free else "")
                + (" (was loss-free)" if was_loss_free and not was_risk_free else ""), "WARN"
            )
            self.sell_pos_ticket = None
            self._sell_confirmed = False
            if was_risk_free or was_loss_free:
                tag = "risk-free" if was_risk_free else "loss-free"
                self._log(
                    f"🔴  [{self.name[:20]}] {tag} SELL closed — "
                    f"resetting to IDLE", "NEW"
                )
                self.needs_full_reset = True
                self.reset()
            elif close_reason == "tp":
                self._log(
                    f"🏆  [{self.name[:20]}] SELL hit TP — round won, "
                    f"resetting to IDLE", "NEW"
                )
                self.needs_full_reset = True
                self.reset()
            else:
                real_loss = self._get_real_loss(closed_ticket)
                if real_loss > 0:
                    self.cumulative_loss += real_loss
                    self._calibrate_pip_value(real_loss, self.sell_lot)
                self._place_new_sell_stop(anchor_price=close_price)

    def _resync_open_sl(self, buy_pos: list, sell_pos: list):
        """
        Re-send TRADE_ACTION_SLTP for any open position whose live SL
        no longer matches the opposite side's REAL current entry price
        (_sell_entry / _buy_entry, both spread-compensated and live).
        This is what makes BUY's SL track exactly where SELL is
        actually sitting right now, and vice versa — the "zero gap"
        mirror the bot is designed around. Spread moves tick to tick,
        so this can re-send fairly often; that's accepted as the cost
        of an exact, always-current mirror rather than a stale one.

        Skipped for a side once R1 (loss-free) or R2 (risk-free) has
        been applied to it — from that point its SL represents a
        locked-in profit level, owned exclusively by
        _check_loss_free / _check_risk_free.
        """
        if (buy_pos and not self.risk_free_applied.get("buy", False)
                and not self.loss_free_applied.get("buy", False)):
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            target_sl = self._buy_sl_price
            if abs(pos.sl - target_sl) > self.pip_size * 0.9:
                self._move_position_sl(pos.ticket, target_sl)

        if (sell_pos and not self.risk_free_applied.get("sell", False)
                and not self.loss_free_applied.get("sell", False)):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            target_sl = self._sell_sl_price
            if abs(pos.sl - target_sl) > self.pip_size * 0.9:
                self._move_position_sl(pos.ticket, target_sl)

    def _resync_open_tp(self, buy_pos: list, sell_pos: list):
        """
        Re-send TRADE_ACTION_SLTP for any open position whose live TP
        no longer matches the freshly-computed balance-target TP (see
        _balance_target_tp_pips). The gap to the GUI's balance-TP%
        target shrinks every round as lot size grows, and can also
        shift from balance changes elsewhere (a sibling line/round
        winning or losing) — so a TP set once at entry time can go
        stale. This keeps it live-adjusted on every poll while the
        position is open.

        Skipped for a side once R1 (loss-free) or R2 (risk-free) has
        been applied to it — at that point its SL/TP no longer
        represent the balance-target goal, they represent a locked-in
        profit level, owned exclusively by _check_loss_free /
        _check_risk_free from then on.
        """
        if (buy_pos and not self.risk_free_applied.get("buy", False)
                and not self.loss_free_applied.get("buy", False)):
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            target_tp = self._buy_tp_price
            # Only bother re-sending if the change is more than a
            # rounding/float-noise difference, to avoid spamming
            # order_send every single scan for a no-op change.
            if abs(pos.tp - target_tp) > self.pip_size * 0.9:
                self._move_position_tp(pos.ticket, target_tp)

        if (sell_pos and not self.risk_free_applied.get("sell", False)
                and not self.loss_free_applied.get("sell", False)):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            target_tp = self._sell_tp_price
            if abs(pos.tp - target_tp) > self.pip_size * 0.9:
                self._move_position_tp(pos.ticket, target_tp)

    def _move_position_tp(self, ticket: int, new_tp: float) -> bool:
        """Modify an open position's TP via TRADE_ACTION_SLTP, keeping
        its current SL untouched."""
        try:
            pos = next((p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == ticket), None)
            if not pos:
                return False
            res = mt5.order_send({
                "action":   mt5.TRADE_ACTION_SLTP,
                "symbol":   self.symbol,
                "position": ticket,
                "sl":       pos.sl,
                "tp":       new_tp,
                "magic":    MAGIC_NUMBER,
            })
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._log(
                    f"🎯  [{self.name[:20]}] #{ticket} TP adjusted to "
                    f"{new_tp:.5f} (balance-target gap update)", "INFO"
                )
                return True
            self._log(
                f"⚠️  [{self.name[:20]}] TP resync failed for #{ticket}: "
                f"{getattr(res, 'comment', 'unknown error')}", "WARN"
            )
            return False
        except Exception as e:
            log.warning("TP resync error: %s", e)
            return False

    def _check_loss_free(self, buy_pos: list, sell_pos: list):
        """
        R1 — Loss-Free. Once an open position's floating profit
        reaches LOSS_FREE_TRIGGER_R (default 1R), move its SL to
        breakeven (its own entry price) — guaranteeing this round
        can no longer lose money, win or flat at worst.

        Mirrors _check_risk_free's structure exactly, one trigger
        level lower. If R2 (risk-free) later also fires for the same
        side, it simply overwrites the SL again (R2 owns the SL once
        triggered — see the skip guards in _check_risk_free /
        _resync_open_sl/tp).

        Item 9: once applied, the trader can drag the bot-drawn
        TB4_R1_<name> chart rectangle to a different price; the
        watcher reads it back each scan into
        self.override_r1_price[side], and that value is used here
        instead of recalculating breakeven, for as long as it stays
        != None.
        """
        if not self._loss_free_enabled:
            return
        trigger_r = getattr(cfg, "LOSS_FREE_TRIGGER_R", 1.0)

        if (buy_pos and not self.loss_free_applied.get("buy", False)
                and not self.risk_free_applied.get("buy", False)):
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            r = self.buy_r_frozen
            if r > 0:
                profit_dist = pos.price_current - pos.price_open
                if profit_dist >= trigger_r * r:
                    new_sl = self.override_r1_price.get("buy") or pos.price_open
                    new_sl = _round_price(new_sl, self.symbol)
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.loss_free_applied["buy"] = True
                        self._log(
                            f"🟩  [{self.name[:20]}] BUY loss-free (R1) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL moved to breakeven {new_sl:.5f}", "NEW"
                        )

        if (sell_pos and not self.loss_free_applied.get("sell", False)
                and not self.risk_free_applied.get("sell", False)):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            r = self.sell_r_frozen
            if r > 0:
                profit_dist = pos.price_open - pos.price_current
                if profit_dist >= trigger_r * r:
                    new_sl = self.override_r1_price.get("sell") or pos.price_open
                    new_sl = _round_price(new_sl, self.symbol)
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.loss_free_applied["sell"] = True
                        self._log(
                            f"🟩  [{self.name[:20]}] SELL loss-free (R1) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL moved to breakeven {new_sl:.5f}", "NEW"
                        )

        # ── Trader-adjusted override, post-application ─────────────
        # Once R1 is applied, keep tracking the chart line in case the
        # trader drags it to a different price (item 9).
        if self.loss_free_applied.get("buy", False) and buy_pos:
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            ov = self.override_r1_price.get("buy")
            if ov is not None and abs(pos.sl - ov) > self.pip_size * 0.9:
                self._move_position_sl(pos.ticket, _round_price(ov, self.symbol))

        if self.loss_free_applied.get("sell", False) and sell_pos:
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            ov = self.override_r1_price.get("sell")
            if ov is not None and abs(pos.sl - ov) > self.pip_size * 0.9:
                self._move_position_sl(pos.ticket, _round_price(ov, self.symbol))

    def _check_risk_free(self, buy_pos: list, sell_pos: list):
        """
        R2 — Risk-Free + Partial Exit. Once an open position's
        floating profit reaches RISK_FREE_TRIGGER_R (default 2R):

          1. If PARTIAL_EXIT_ENABLED, immediately close
             PARTIAL_EXIT_RATIO (default 70%) of the position's
             volume as a real deal — banking that profit right now,
             not just on paper.
          2. Move the SL on whatever volume remains (the full
             position if partial exit was skipped/unavailable at
             this size) to lock in enough profit to cover ALL
             cumulative losses taken so far this cycle PLUS this
             round's own risk — not just a flat +1R. A flat +1R badly
             under-covers deep runs: by the time lot has grown
             several touches, cumulative_loss can be many multiples
             of any single round's R.

        The remaining volume keeps running toward R3 (TP) with its
        SL already locked — a classic scale-out: bank most of the
        winner now, let a smaller "runner" chase the bigger target.

        TRIGGER uses the position's own frozen R (self.buy_r_frozen/
        sell_r_frozen) — that's the right basis for "has this round
        itself moved favorably enough to act."

        LOCK-IN AMOUNT (how far to move SL) uses:
            total_at_risk_dollars = cumulative_loss + (R in dollars
                                    at the position's own lot)
        converted to a price distance via the REAL dollar-per-pip at
        the REMAINING lot (post-partial-close, if it happened), so
        the dollar amount actually locked in still matches the real
        cumulative loss regardless of how much volume is left.

        IMPORTANT (lessons from an earlier, buggy prototype of partial
        exit): the remaining/reduced volume after a partial close is
        used ONLY for this lock-distance calculation. It must NEVER
        be used to size the next round's order — that's controlled
        exclusively by the soft-lot table indexed by touch_count
        (see _next_table_lot), completely independent of any live
        position's volume. Don't wire pos.volume into that path.

        Item 9: once applied, the trader can drag the bot-drawn
        TB4_R2_<name> chart rectangle; the watcher reads it back into
        self.override_r2_price[side] each scan, and that value is
        used instead of the calculated lock price for as long as
        it's set.

        R is read from self.buy_r_frozen/sell_r_frozen — frozen ONCE
        at position-confirmation time (see _check_legs), not
        recomputed from the live, continuously-resynced pos.sl field.
        """
        if not self._risk_free_enabled:
            return
        trigger_r = getattr(cfg, "RISK_FREE_TRIGGER_R", 2.0)

        if buy_pos and not self.risk_free_applied.get("buy", False):
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            r = self.buy_r_frozen
            if r > 0:
                profit_dist = pos.price_current - pos.price_open
                if profit_dist >= trigger_r * r:
                    lot_for_lock = pos.volume
                    if getattr(cfg, "PARTIAL_EXIT_ENABLED", False):
                        remain = self._partial_close_position(
                            pos.ticket, getattr(cfg, "PARTIAL_EXIT_RATIO", 0.70))
                        if remain is not None:
                            lot_for_lock = remain
                    if self.override_r2_price.get("buy") is not None:
                        new_sl = _round_price(self.override_r2_price["buy"], self.symbol)
                    else:
                        lock_dist = self._risk_free_lock_distance(r, lot_for_lock)
                        new_sl = _round_price(pos.price_open + lock_dist, self.symbol)
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.risk_free_applied["buy"] = True
                        self._log(
                            f"🛡️  [{self.name[:20]}] BUY risk-free (R2) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL moved to {new_sl:.5f} "
                            f"(covers cumulative_loss=${self.cumulative_loss:.2f} "
                            f"+ this round's risk)", "NEW"
                        )

        if sell_pos and not self.risk_free_applied.get("sell", False):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            r = self.sell_r_frozen
            if r > 0:
                profit_dist = pos.price_open - pos.price_current
                if profit_dist >= trigger_r * r:
                    lot_for_lock = pos.volume
                    if getattr(cfg, "PARTIAL_EXIT_ENABLED", False):
                        remain = self._partial_close_position(
                            pos.ticket, getattr(cfg, "PARTIAL_EXIT_RATIO", 0.70))
                        if remain is not None:
                            lot_for_lock = remain
                    if self.override_r2_price.get("sell") is not None:
                        new_sl = _round_price(self.override_r2_price["sell"], self.symbol)
                    else:
                        lock_dist = self._risk_free_lock_distance(r, lot_for_lock)
                        new_sl = _round_price(pos.price_open - lock_dist, self.symbol)
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.risk_free_applied["sell"] = True
                        self._log(
                            f"🛡️  [{self.name[:20]}] SELL risk-free (R2) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL moved to {new_sl:.5f} "
                            f"(covers cumulative_loss=${self.cumulative_loss:.2f} "
                            f"+ this round's risk)", "NEW"
                        )

        # ── Trader-adjusted override, post-application ─────────────
        if self.risk_free_applied.get("buy", False) and buy_pos:
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            ov = self.override_r2_price.get("buy")
            if ov is not None and abs(pos.sl - ov) > self.pip_size * 0.9:
                self._move_position_sl(pos.ticket, _round_price(ov, self.symbol))

        if self.risk_free_applied.get("sell", False) and sell_pos:
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            ov = self.override_r2_price.get("sell")
            if ov is not None and abs(pos.sl - ov) > self.pip_size * 0.9:
                self._move_position_sl(pos.ticket, _round_price(ov, self.symbol))

    def _risk_free_lock_distance(self, r_price: float, lot: float) -> float:
        """
        Price distance (always positive) the risk-free SL should sit
        beyond entry, sized so the locked-in dollar profit covers
        cumulative_loss (all real losses taken so far this cycle)
        PLUS this round's own risk in dollars — not just a flat +1R.

        total_at_risk_$ = cumulative_loss + (r_price_in_pips × $/pip)
        lock_distance    = total_at_risk_$ / $/pip   (back to price units)

        Falls back to the plain +1R distance if dollar-per-pip can't
        be determined, so a lock is always produced.
        """
        dpp = self._dollar_per_pip(lot)
        if dpp <= 0:
            return r_price  # fallback: plain +1R in price terms

        r_pips = r_price / self.pip_size
        this_round_risk_dollars = r_pips * dpp
        total_at_risk_dollars   = self.cumulative_loss + this_round_risk_dollars

        lock_pips = total_at_risk_dollars / dpp
        return lock_pips * self.pip_size

    def _move_position_sl(self, ticket: int, new_sl: float) -> bool:
        """Modify an open position's SL via TRADE_ACTION_SLTP."""
        try:
            pos = next((p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == ticket), None)
            if not pos:
                return False
            res = mt5.order_send({
                "action":   mt5.TRADE_ACTION_SLTP,
                "symbol":   self.symbol,
                "position": ticket,
                "sl":       new_sl,
                "tp":       pos.tp,
                "magic":    MAGIC_NUMBER,
            })
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                return True
            self._log(
                f"⚠️  [{self.name[:20]}] risk-free SL move failed for #{ticket}: "
                f"{getattr(res, 'comment', 'unknown error')}", "WARN"
            )
            return False
        except Exception as e:
            log.warning("Risk-free SL move error: %s", e)
            return False

    def _partial_close_position(self, ticket: int, close_fraction: float):
        """
        Close `close_fraction` of an open position's volume via a
        real opposite-side deal against it (same mechanism MT5 uses
        for any partial close — same ticket survives with reduced
        volume). Returns the resulting remaining volume on success,
        or None if skipped/failed.

        Respects the symbol's volume_step/volume_min — if either the
        slice to close or what would remain falls below volume_min
        once rounded to volume_step, the partial close is skipped
        entirely (not attempted at a wrong size) and the position is
        left at full volume for the caller to handle normally.
        """
        try:
            pos = next((p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == ticket), None)
            if not pos:
                return None

            info = mt5.symbol_info(self.symbol)
            step = getattr(info, "volume_step", 0.01) or 0.01
            vmin = getattr(info, "volume_min", 0.01) or 0.01

            raw_close  = pos.volume * close_fraction
            close_vol  = round(raw_close / step) * step
            close_vol  = round(close_vol, 2)
            remain_vol = round(pos.volume - close_vol, 2)

            if close_vol < vmin or remain_vol < vmin:
                self._log(
                    f"ℹ️  [{self.name[:20]}] partial exit skipped for #{ticket} "
                    f"(vol={pos.volume:.2f} too small to split at "
                    f"{close_fraction*100:.0f}% / volume_min={vmin}) — "
                    f"keeping full position, normal risk-free SL still applies",
                    "INFO"
                )
                return None

            is_buy  = pos.type == 0
            tick    = mt5.symbol_info_tick(self.symbol)
            filling = _filling_mode(self.symbol)
            res = mt5.order_send({
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       self.symbol,
                "volume":       close_vol,
                "type":         mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "position":     ticket,
                "price":        tick.bid if is_buy else tick.ask,
                "deviation":    30,
                "magic":        MAGIC_NUMBER,
                "comment":      "TB4_PartialExit",
                "type_filling": filling,
            })
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._log(
                    f"💵  [{self.name[:20]}] partial exit #{ticket} | "
                    f"closed {close_vol:.2f} lot ({close_fraction*100:.0f}%) | "
                    f"{remain_vol:.2f} lot still running to TP", "NEW"
                )
                return remain_vol
            self._log(
                f"⚠️  [{self.name[:20]}] partial exit failed for #{ticket}: "
                f"{getattr(res, 'comment', 'unknown error')} — "
                f"keeping full position, normal risk-free SL still applies", "WARN"
            )
            return None
        except Exception as e:
            log.warning("Partial exit error: %s", e)
            return None

    def _get_close_info(self, position_ticket: int):
        """
        Fetch the exact execution price AND the reason (SL/TP/manual/
        other) of the deal that closed this position. Returns
        (price, reason) where reason is one of "tp", "sl", "manual",
        "other", or None if the deal can't be found.

        Distinguishing TP from SL matters a lot here: a TP hit means
        that round was WON outright — the position should reset to
        IDLE, not chain into another martingale recovery order at a
        bigger lot. Only an SL hit (a loss) should trigger the normal
        double-up recovery cycle. Treating every close the same way
        (as this used to) meant a winning TP close would immediately
        re-arm a new pending stop anyway, which could go on to lose
        and eat into profit that was already locked in.
        """
        try:
            deals = mt5.history_deals_get(position=position_ticket)
            if not deals:
                return None, None
            closing = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
            if not closing:
                return None, None
            closing.sort(key=lambda d: d.time, reverse=True)
            deal = closing[0]
            price = float(deal.price)
            d_reason = getattr(deal, "reason", None)
            if d_reason == mt5.DEAL_REASON_TP:
                reason = "tp"
            elif d_reason == mt5.DEAL_REASON_SL:
                reason = "sl"
            elif d_reason in (mt5.DEAL_REASON_CLIENT, mt5.DEAL_REASON_MOBILE,
                              mt5.DEAL_REASON_WEB, mt5.DEAL_REASON_EXPERT):
                reason = "manual"
            else:
                reason = "other"
            return price, reason
        except Exception as e:
            log.warning("Could not fetch close info for #%s: %s",
                       position_ticket, e)
            return None, None

    def _get_close_price(self, position_ticket: int):
        """Back-compat wrapper — price only. See _get_close_info."""
        price, _ = self._get_close_info(position_ticket)
        return price

    def _get_real_loss(self, position_ticket: int) -> float:
        """
        Return the absolute dollar loss of a closed position from MT5
        deal history. Returns 0.0 if the close was profitable or if
        the deal can't be found — so it's safe to always add the
        return value to cumulative_loss.
        """
        try:
            deals = mt5.history_deals_get(position=position_ticket)
            if not deals:
                return 0.0
            closing = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
            if not closing:
                return 0.0
            closing.sort(key=lambda d: d.time, reverse=True)
            profit = float(closing[0].profit)
            return abs(profit) if profit < 0 else 0.0
        except Exception:
            return 0.0

    # ── New stop placement ────────────────────────────────────────

    def _max_affordable_lot(self, lot_step: float = 0.01) -> float:
        """
        Largest lot size (rounded down to lot_step) that passes
        _can_afford's margin check right now. Returns 0.0 if even the
        minimum lot isn't affordable.
        """
        try:
            acct = mt5.account_info()
            tick = mt5.symbol_info_tick(self.symbol)
            if not acct or not tick:
                return 0.0
            # Margin scales ~linearly with lot for a fixed price/symbol,
            # so compute margin-per-lot from a 1.0-lot probe and divide.
            probe_margin = mt5.order_calc_margin(
                mt5.ORDER_TYPE_BUY, self.symbol, 1.0, tick.ask
            )
            if not probe_margin or probe_margin <= 0:
                return 0.0
            free_margin   = acct.margin_free
            equity        = acct.equity
            safety_margin = equity * 0.05
            usable_margin = free_margin - safety_margin
            if usable_margin <= 0:
                return 0.0
            max_lot = usable_margin / probe_margin
            # Round down to the nearest lot_step, floor at 0.
            steps = int(max_lot / lot_step)
            return max(steps * lot_step, 0.0)
        except Exception as e:
            log.warning("Max affordable lot calc error: %s", e)
            return 0.0

    def _has_bounce_confluence(self, is_buy: bool, current_price: float) -> bool:
        """
        Structural gate for deep martingale rounds: before continuing
        to double into a losing position, check whether real market
        structure (OB+FVG confluence) actually supports a bounce in
        the needed direction near the current price.

        This is a reasoned heuristic, NOT a calibrated probability —
        there's no backtested statistic backing a specific win rate
        here. It simply asks: does a genuine, currently-unmitigated
        OB+FVG confluence zone exist within a reasonable distance of
        price, in the direction (BULL for a BUY recovery, BEAR for a
        SELL recovery) that would actually support the bounce this
        round needs? If yes, the cycle continues; if no, the round
        is treated as unsupported and the cycle resets instead of
        blindly doubling again.

        Only called once lot/margin thresholds are met — see the
        call sites in _place_new_buy_stop/_place_new_sell_stop.
        """
        try:
            from core.ob_detector import detect_order_blocks
            from core.ob_fvg_confluence import find_confluences
            from core.mtf_fvg import _scan_fvgs, TIMEFRAME_SPECS

            obs = detect_order_blocks(
                self.symbol, lookback=200, min_impulse_pips=3.0, swing_lookback=5
            )
            tick = mt5.symbol_info_tick(self.symbol)
            if not tick:
                return True  # can't check — fail open, don't block on a data gap
            bid, ask = tick.bid, tick.ask

            spec = TIMEFRAME_SPECS["1M"]
            fvgs = _scan_fvgs(
                self.symbol, "1M", spec["default_lookback"],
                min_gap_pips=1.5, pip_size=self.pip_size, bid=bid, ask=ask
            )

            zones = find_confluences(obs, fvgs, self.pip_size)
            if not zones:
                return False

            wanted_kind = "BULL" if is_buy else "BEAR"
            # Reasonable proximity: within 3x the rectangle height —
            # close enough to plausibly matter for this round's
            # bounce, not just any zone anywhere on the chart.
            max_dist = (self.rect_top - self.rect_bottom) * 3

            for z in zones:
                if z.kind != wanted_kind or z.mitigated:
                    continue
                zone_mid = (z.combined_top + z.combined_bottom) / 2
                if abs(zone_mid - current_price) <= max_dist:
                    self._log(
                        f"✅  [{self.name[:20]}] bounce confluence found: "
                        f"{z.summary()}", "INFO"
                    )
                    return True

            return False
        except Exception as e:
            log.warning("Bounce confluence check error: %s", e)
            return True  # fail open on any error — don't block on a bug here

    def _can_afford(self, lot: float, is_buy: bool) -> bool:
        try:
            action = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
            tick   = mt5.symbol_info_tick(self.symbol)
            price  = tick.ask if is_buy else tick.bid
            margin = mt5.order_calc_margin(action, self.symbol, lot, price)
            acct   = mt5.account_info()
            if margin is None or acct is None:
                return True
            free_margin   = acct.margin_free
            equity        = acct.equity
            # 5% cushion — enough to avoid landing right at a literal
            # margin call after this fill, without blocking trades the
            # account can genuinely afford. The previous 20% buffer
            # was blocking real, affordable rounds (e.g. needing
            # $2122 against $2445 free — actually affordable — got
            # blocked because the 20%-of-equity cushion demanded $706
            # left over, not because the trade itself was unaffordable).
            safety_margin = equity * 0.05
            if free_margin - margin < safety_margin:
                self._log(
                    f"🛡️  [{self.name[:20]}] R{self.round} MARGIN PROTECTION | "
                    f"lot={lot:.2f} needs ${margin:.2f} margin | "
                    f"free=${free_margin:.2f} equity=${equity:.2f} | "
                    f"cannot place safely — resetting to IDLE", "WARN"
                )
                return False
            return True
        except Exception as e:
            log.warning("Margin check error: %s", e)
            return True

    def _place_new_buy_stop(self, anchor_price: float = None):
        self.round  += 1
        next_lot = self._next_table_lot(base_lot=self.sell_lot)
        if next_lot is None:
            return  # kill switch tripped
        self.buy_lot = next_lot

        if not self._can_afford(self.buy_lot, is_buy=True):
            # Table lot isn't affordable — try the largest lot that
            # IS affordable instead of abandoning the cycle. The
            # TP/SL formulas read self.buy_lot live, so reducing it
            # here still produces a correctly-sized TP.
            reduced_lot = self._max_affordable_lot()
            if reduced_lot >= 0.01:
                self._log(
                    f"🛡️  [{self.name[:20]}] R{self.round} reduced lot "
                    f"{self.buy_lot:.2f} → {reduced_lot:.2f} (margin-limited) — "
                    f"keeping the cycle alive toward the original target", "WARN"
                )
                self.buy_lot = reduced_lot
            else:
                # Even the minimum lot isn't affordable — nothing left
                # to try. Reset cleanly rather than leaving the source
                # dangling with no position and no pending order.
                self._log(
                    f"🛡️  [{self.name[:20]}] R{self.round} MARGIN PROTECTION | "
                    f"not even minimum lot is affordable — resetting to IDLE", "WARN"
                )
                self.needs_full_reset = True
                self.reset()
                return

        # Re-anchor from the EXACT price the previous BUY position's
        # SL closed at (rather than the original fixed rectangle
        # edge), eliminating any slippage-induced gap. Delegated to
        # _reanchor_buy() since subclasses derive entry/SL/TP from
        # different fields and need their own re-anchoring logic.
        if anchor_price is not None:
            self._reanchor_buy(anchor_price)

        # ── Structural gate for deep rounds ─────────────────────────
        # Once lot has grown large (≥0.64) OR free margin is getting
        # tight, don't just blindly continue — require real OB+FVG
        # confluence supporting a bounce. With the soft-lot tables
        # (max 0.11/0.20) this threshold is unlikely to be reached in
        # practice — MAX_TOUCHES will trip the kill switch first —
        # but it's left in place as a backstop in case the tables are
        # ever reconfigured larger.
        deep_round = self.buy_lot >= 0.64
        tight_margin = False
        if not deep_round:
            try:
                acct = mt5.account_info()
                if acct and acct.equity > 0:
                    tight_margin = (acct.margin_free / acct.equity) < 0.30
            except Exception:
                pass

        if deep_round or tight_margin:
            mid_now = ((self._last_bid + self._last_ask) / 2 if self._last_ask
                       else (self.rect_top + self.rect_bottom) / 2)
            if not self._has_bounce_confluence(is_buy=True, current_price=mid_now):
                self._log(
                    f"🚫  [{self.name[:20]}] R{self.round} no bounce confluence "
                    f"found (lot={self.buy_lot:.2f}) — cutting losses, "
                    f"resetting instead of continuing", "WARN"
                )
                self.needs_full_reset = True
                self.reset()
                return

        order = {"type": "BUY_STOP", "entry": self._buy_entry,
                 "sl": self._buy_sl_price, "tp": self._buy_tp_price,
                 "lot": self.buy_lot, "source": self.rect_top, "round": self.round}
        results = send_pair([order], self.symbol)
        ok = [r for r in results if r["ok"]]

        if ok:
            self.buy_ticket = ok[0]["ticket"]
            next_opp = self._next_table_lot(base_lot=self.buy_lot)
            if next_opp is None:
                return  # kill switch tripped (logs/closes handled inside)
            self.sell_lot = next_opp
            pending = {o.ticket for o in (mt5.orders_get(symbol=self.symbol) or [])
                       if o.magic == MAGIC_NUMBER}
            if self.sell_ticket and self.sell_ticket in pending:
                self._modify_order_lot(self.sell_ticket, self.sell_lot,
                                       exact_sl=self._sell_sl_price)
            self._log(
                f"🔁  [{self.name[:20]}] R{self.round} new BUY-STOP | "
                f"entry={self._buy_entry:.5f} sl={self._buy_sl_price:.5f} "
                f"lot={self.buy_lot:.2f} | SELL lot (touch {self.touch_count}) → {self.sell_lot:.2f}", "NEW"
            )
            _save(self)

    def _place_new_sell_stop(self, anchor_price: float = None):
        self.round   += 1
        next_lot = self._next_table_lot(base_lot=self.buy_lot)
        if next_lot is None:
            return  # kill switch tripped
        self.sell_lot = next_lot

        if not self._can_afford(self.sell_lot, is_buy=False):
            reduced_lot = self._max_affordable_lot()
            if reduced_lot >= 0.01:
                self._log(
                    f"🛡️  [{self.name[:20]}] R{self.round} reduced lot "
                    f"{self.sell_lot:.2f} → {reduced_lot:.2f} (margin-limited) — "
                    f"keeping the cycle alive toward the original target", "WARN"
                )
                self.sell_lot = reduced_lot
            else:
                self._log(
                    f"🛡️  [{self.name[:20]}] R{self.round} MARGIN PROTECTION | "
                    f"not even minimum lot is affordable — resetting to IDLE", "WARN"
                )
                self.needs_full_reset = True
                self.reset()
                return

        # Re-anchor from the EXACT price the previous SELL position's
        # SL closed at — see matching comment in _place_new_buy_stop.
        if anchor_price is not None:
            self._reanchor_sell(anchor_price)

        deep_round = self.sell_lot >= 0.64
        tight_margin = False
        if not deep_round:
            try:
                acct = mt5.account_info()
                if acct and acct.equity > 0:
                    tight_margin = (acct.margin_free / acct.equity) < 0.30
            except Exception:
                pass

        if deep_round or tight_margin:
            mid_now = ((self._last_bid + self._last_ask) / 2 if self._last_ask
                       else (self.rect_top + self.rect_bottom) / 2)
            if not self._has_bounce_confluence(is_buy=False, current_price=mid_now):
                self._log(
                    f"🚫  [{self.name[:20]}] R{self.round} no bounce confluence "
                    f"found (lot={self.sell_lot:.2f}) — cutting losses, "
                    f"resetting instead of continuing", "WARN"
                )
                self.needs_full_reset = True
                self.reset()
                return

        order = {"type": "SELL_STOP", "entry": self._sell_entry,
                 "sl": self._sell_sl_price, "tp": self._sell_tp_price,
                 "lot": self.sell_lot, "source": self.rect_bottom, "round": self.round}
        results = send_pair([order], self.symbol)
        ok = [r for r in results if r["ok"]]

        if ok:
            self.sell_ticket = ok[0]["ticket"]
            next_opp = self._next_table_lot(base_lot=self.sell_lot)
            if next_opp is None:
                return  # kill switch tripped (logs/closes handled inside)
            self.buy_lot = next_opp
            pending = {o.ticket for o in (mt5.orders_get(symbol=self.symbol) or [])
                       if o.magic == MAGIC_NUMBER}
            if self.buy_ticket and self.buy_ticket in pending:
                self._modify_order_lot(self.buy_ticket, self.buy_lot,
                                       exact_sl=self._buy_sl_price)
            self._log(
                f"🔁  [{self.name[:20]}] R{self.round} new SELL-STOP | "
                f"entry={self._sell_entry:.5f} sl={self._sell_sl_price:.5f} "
                f"lot={self.sell_lot:.2f} | BUY lot (touch {self.touch_count}) → {self.buy_lot:.2f}", "NEW"
            )
            _save(self)

    # ── Order lot modification ────────────────────────────────────

    def _modify_order_lot(self, ticket: int, new_lot: float,
                          exact_sl: float = None) -> bool:
        orders = mt5.orders_get(symbol=self.symbol) or []
        target = next((o for o in orders if o.ticket == ticket), None)
        if not target:
            self._log(
                f"ℹ️  [{self.name[:20]}] order #{ticket} already filled", "INFO"
            )
            return False

        cancel_order(ticket)

        is_buy     = target.type == mt5.ORDER_TYPE_BUY_STOP
        order_type = mt5.ORDER_TYPE_BUY_STOP if is_buy else mt5.ORDER_TYPE_SELL_STOP
        filling    = _filling_mode(self.symbol)
        use_sl     = exact_sl if exact_sl is not None else target.sl
        entry      = target.price_open

        tick         = mt5.symbol_info_tick(self.symbol)
        bid          = tick.bid if tick else 0.0
        ask          = tick.ask if tick else 0.0
        already_past = (is_buy and ask > 0 and entry <= ask) or \
                       (not is_buy and bid > 0 and entry >= bid)

        if already_past:
            market_price = ask if is_buy else bid
            use_tp = self._buy_tp_price if is_buy else self._sell_tp_price
            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       self.symbol,
                "volume":       new_lot,
                "type":         mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
                "price":        market_price,
                "sl":           use_sl,
                "tp":           use_tp,
                "deviation":    30,
                "magic":        MAGIC_NUMBER,
                "comment":      (target.comment or "") + "m",
                "type_filling": filling,
            }
            self._log(
                f"⚡  [{self.name[:20]}] {'BUY' if is_buy else 'SELL'} past market — "
                f"MARKET lot={new_lot:.2f} sl={use_sl:.5f}", "WARN"
            )
        else:
            use_tp = self._buy_tp_price if is_buy else self._sell_tp_price
            request = {
                "action":       mt5.TRADE_ACTION_PENDING,
                "symbol":       self.symbol,
                "volume":       new_lot,
                "type":         order_type,
                "price":        entry,
                "sl":           use_sl,
                "tp":           use_tp,
                "deviation":    20,
                "magic":        MAGIC_NUMBER,
                "comment":      (target.comment or "") + "m",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }

        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            if is_buy:
                self.buy_ticket = res.order
            else:
                self.sell_ticket = res.order
            self._log(
                f"✏️  [{self.name[:20]}] {'BUY' if is_buy else 'SELL'}-STOP modified | "
                f"ticket#{res.order} lot={new_lot:.2f} sl={use_sl:.5f} @ {entry:.5f}",
                "INFO"
            )
            return True
        else:
            self._log(
                f"❌  Modify failed: {res.retcode if res else '?'}", "ERROR"
            )
            return False

    # ── Reset ─────────────────────────────────────────────────────

    def reset(self):
        for ticket in [self.buy_ticket, self.sell_ticket]:
            if ticket:
                cancel_order(ticket)
        self.buy_ticket      = None
        self.sell_ticket     = None
        self.buy_pos_ticket  = None
        self.sell_pos_ticket = None
        self.buy_lot         = self.base_lot
        self.sell_lot        = self.base_lot
        self.buy_sl          = None
        self.sell_sl         = None
        self.buy_r_frozen    = 0.0
        self.sell_r_frozen   = 0.0
        self.round           = 0
        self.touch_count     = 0
        self.state           = self.IDLE
        self._buy_confirmed  = False
        self._sell_confirmed = False
        self.risk_free_applied = {"buy": False, "sell": False}
        self.loss_free_applied = {"buy": False, "sell": False}
        self.override_r1_price = {"buy": None, "sell": None}
        self.override_r2_price = {"buy": None, "sell": None}
        self.cumulative_loss   = 0.0
        self._pip_value_per_base_lot = 0.0
        self._log(f"🔄  [{self.name[:20]}] state reset to IDLE")
        try:
            from core.resume import clear_session
            clear_session(self.symbol)
        except Exception:
            pass

    @property
    def summary(self) -> dict:
        return {
            "name":      self.name,
            "rect_top":    self.rect_top,
            "rect_bottom": self.rect_bottom,
            "state":     self.state,
            "round":     self.round,
            "touch":     self.touch_count,
            "direction": f"B:{self.buy_lot:.2f} S:{self.sell_lot:.2f}",
            "lot":       self.buy_lot,
            "buy_lot":   self.buy_lot,
            "sell_lot":  self.sell_lot,
        }