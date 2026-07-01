"""
core/position_geometry.py — part of core/position_monitor.SourceState, split out for
file size (see core/position_monitor.py for the assembled class).

DO NOT instantiate _GeometryMixin directly — it is a mixin, combined
with the other position_* mixins into the real SourceState class in
core/position_monitor.py. Methods here freely call self.<method>()
defined in OTHER mixins (geometry/helpers/protection/recovery/main) —
that's safe and intentional: once mixed into one class, every method
is on the same namespace regardless of which file defined it.
"""
import logging
import time as _time
import MetaTrader5 as mt5
import config as cfg
from config import MAGIC_NUMBER
from core.order_manager import send_pair, cancel_order, _filling_mode, _round_price
from core.position_monitor_base import log, ACTIVATION_GRACE_SEC, _save


class _GeometryMixin:
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

    @property
    def _buy_entry(self):
        """BUY_STOP entry price = EXACTLY the rectangle's top edge,
        zero spread adjustment. (Previously subtracted the live
        spread so the fill would land exactly on rect_top despite
        BUY_STOP filling at ask — removed by request: the trader
        wants entry and SL both pinned to the literal rectangle line
        with no adjustment, so that e.g. SELL's actual position price
        (this same value) lands exactly on BUY's SL, with zero gap
        between them. See _buy_sl_price/_sell_sl_price.)"""
        return _round_price(self.rect_top, self.symbol)

    @property
    def _sell_entry(self):
        """SELL_STOP entry price = EXACTLY the rectangle's bottom
        edge, zero spread adjustment. See _buy_entry — same reasoning,
        mirrored."""
        return _round_price(self.rect_bottom, self.symbol)

    @property
    def _buy_sl_price(self):
        """
        SL of BUY = ALWAYS EXACTLY the rectangle's bottom edge.

        The rectangle is the fixed source of truth — never the live
        (possibly slipped) price of the opposite position. A
        triggered stop order can fill a few points off the requested
        price under Market Execution, but that's a broker-side fact
        about where SELL happened to land, not a reason to move BUY's
        SL away from the rectangle. Pinning both SLs to the rectangle
        edges, always, keeps R (=rectangle height) and every R1/R2/R3
        calculation exactly consistent regardless of individual fill
        slippage. _resync_open_sl reads this every scan and re-applies
        it if anything has drifted even 1 point, so this self-corrects
        continuously rather than only being set once at placement.
        """
        return _round_price(self.rect_bottom, self.symbol)

    @property
    def _sell_sl_price(self):
        """SL of SELL = ALWAYS EXACTLY the rectangle's top edge.
        See _buy_sl_price — same reasoning, mirrored."""
        return _round_price(self.rect_top, self.symbol)

    def _reanchor_buy(self, close_price: float):
        """
        No-op in the base class. rect_top/rect_bottom are the fixed
        anchors and must NEVER move — every new recovery order keeps
        using the same rectangle edges. SL is pinned exactly to the
        opposite rectangle edge with zero spread adjustment (see
        _buy_sl_price/_sell_sl_price) and entries are independently
        spread-compensated (see _buy_entry/_sell_entry) — there is no
        slippage gap to correct here since every price involved is
        derived directly from the same two fixed, unmoving edges.
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
            price = (tick.bid + tick.ask) / \
                2.0 if tick else (self.rect_top + self.rect_bottom) / 2.0
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

        # Last resort: use cached value from a real closed SL.
        # _pip_value_per_base_lot is $/pip at base_lot (0.01).
        # Scale correctly: dpp(lot) = calibrated * (lot / base_lot)
        if getattr(self, '_pip_value_per_base_lot', 0.0) > 0:
            base = getattr(self, 'base_lot', 0.01) or 0.01
            return self._pip_value_per_base_lot * (lot / base)
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

        rect_pips = (self.rect_top - self.rect_bottom) / self.pip_size
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
        if getattr(self, 'tp_free', False):
            return 0.0  # TP-Free mode: no take-profit on any order
        tp_dist = self._tp_pips * self.pip_size
        return _round_price(self._buy_entry + tp_dist, self.symbol)

    @property
    def _sell_tp_price(self):
        if getattr(self, 'tp_free', False):
            return 0.0  # TP-Free mode: no take-profit on any order
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

    def _min_required_height(self) -> float:
        """
        The broker's minimum stop distance (trade_stops_level), in
        price units, with a small safety margin. Since SL now sits
        EXACTLY on the opposite rectangle edge with zero buffer (see
        _buy_sl_price/_sell_sl_price), the rectangle's own height is
        the ONLY distance between an entry and its SL — if that's
        smaller than what the broker requires, every order for this
        rectangle will be rejected outright ('Invalid stops'/'Invalid
        SL/TP'). Checked once at touch time so that failure is a
        clear, specific log message instead of a confusing broker
        rejection after the fact.
        """
        try:
            info = mt5.symbol_info(self.symbol)
            if info and getattr(info, "trade_stops_level", 0) > 0:
                return info.trade_stops_level * info.point * 1.2  # 20% safety margin
        except Exception:
            pass
        return 0.0

    def _risk_free_lock_distance(self, r_price: float, lot: float, multiplier: float = 1.0) -> float:
        """
        Price distance (always positive) the loss-free/risk-free SL
        should sit beyond entry, sized so the locked-in dollar profit
        covers `multiplier` × (cumulative_loss + this round's own
        risk) — not just a flat +1R.

        The R-tier multiplier pattern, by explicit design: R1
        (loss-free) covers the session's loss ×1 (multiplier=1.0,
        the default — see _check_loss_free), R2 (risk-free) covers it
        ×2 (see _check_risk_free), R3 (the balance-target TP) already
        independently targets ×3 via its own RR formula
        (_tp_pips) — R1/R2/R3 covering 1x/2x/3x the session's risk is
        a deliberately consistent progression across all three tiers.

        total_at_risk_$ = multiplier × (cumulative_loss + r_price_in_pips × $/pip)
        lock_distance    = total_at_risk_$ / $/pip   (back to price units)

        Falls back to the plain +1R distance (ignoring the multiplier)
        if dollar-per-pip can't be determined, so a lock is always
        produced.
        """
        dpp = self._dollar_per_pip(lot)
        if dpp <= 0:
            return r_price  # fallback: plain +1R in price terms

        r_pips = r_price / self.pip_size
        this_round_risk_dollars = r_pips * dpp
        total_at_risk_dollars = multiplier * \
            (self.cumulative_loss + this_round_risk_dollars)

        lock_pips = total_at_risk_dollars / dpp
        return lock_pips * self.pip_size

    def _clamp_sl_to_valid_range(self, new_sl: float, pos, is_buy: bool):
        """
        Cap a calculated risk-free lock price to whatever the broker
        will actually accept, instead of sending an invalid request
        every scan forever.

        The lock-distance formula (_risk_free_lock_distance) spreads a
        FIXED dollar amount (cumulative_loss + this round's risk) over
        whatever lot remains — and after a 70% partial exit, that
        remaining lot can be small enough that the required price
        distance overshoots past the current market price (or even
        past the position's own TP), which every broker rejects as
        'Invalid stops'. Caught live: a position needing to lock $0.48
        on a 0.01-lot remainder required an 4.8-pip move, more than
        the position's own profit distance at the time.

        Returns (clamped_price, was_clamped). Never clamps below the
        position's own entry (breakeven) — risk-free must never
        produce a worse outcome than loss-free already would.
        """
        try:
            info = mt5.symbol_info(self.symbol)
            min_buf = (info.trade_stops_level * info.point *
                       1.5) if info and info.trade_stops_level else (self.pip_size * 0.5)
        except Exception:
            min_buf = self.pip_size * 0.5

        clamped = False
        if is_buy:
            ceiling = pos.price_current - min_buf
            if pos.tp and pos.tp > 0:
                ceiling = min(ceiling, pos.tp - min_buf)
            if new_sl > ceiling:
                new_sl = ceiling
                clamped = True
            new_sl = max(new_sl, pos.price_open)  # never worse than breakeven
        else:
            floor = pos.price_current + min_buf
            if pos.tp and pos.tp > 0:
                floor = max(floor, pos.tp + min_buf)
            if new_sl < floor:
                new_sl = floor
                clamped = True
            new_sl = min(new_sl, pos.price_open)  # never worse than breakeven

        return _round_price(new_sl, self.symbol), clamped