"""
core/position_recovery.py — part of core/position_monitor.SourceState, split out for
file size (see core/position_monitor.py for the assembled class).

DO NOT instantiate _RecoveryMixin directly — it is a mixin, combined
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


class _RecoveryMixin:
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

    def _place_new_buy_stop(self, anchor_price: float = None):
        self.round += 1
        next_lot = self._next_table_lot(base_lot=self.sell_lot)
        if next_lot is None:
            return  # kill switch tripped
        self.buy_lot = next_lot
        # Modes 1/2: ONE table step per round, shared by both legs (item
        # 3/6 — previously each round consumed 2 steps, one here and a
        # second one further down for the opposite leg, making the
        # soft-lot tables step by +0.02/round instead of the intended
        # +0.01/round for Mode 1). Mode 3 still gets its own independent
        # doubling call further down, matching the original martingale.
        if self.soft_lot_mode != 3:
            self.sell_lot = self.buy_lot

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
            if self.soft_lot_mode == 3:
                next_opp = self._next_table_lot(base_lot=self.buy_lot)
                if next_opp is None:
                    return  # kill switch tripped (logs/closes handled inside)
                self.sell_lot = next_opp
            # Modes 1/2: self.sell_lot was already set to the same
            # value as self.buy_lot above (one table step shared by
            # both legs this round) — just push it to the pending
            # order below if there is one.
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
        self.round += 1
        next_lot = self._next_table_lot(base_lot=self.buy_lot)
        if next_lot is None:
            return  # kill switch tripped
        self.sell_lot = next_lot
        # Modes 1/2: ONE table step per round, shared by both legs —
        # see the matching comment in _place_new_buy_stop (item 3/6).
        if self.soft_lot_mode != 3:
            self.buy_lot = self.sell_lot

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
            if self.soft_lot_mode == 3:
                next_opp = self._next_table_lot(base_lot=self.sell_lot)
                if next_opp is None:
                    return  # kill switch tripped (logs/closes handled inside)
                self.buy_lot = next_opp
            # Modes 1/2: self.buy_lot already shares this round's lot —
            # see comment above.
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
