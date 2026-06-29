"""
core/position_entry.py — part of core/position_monitor.SourceState, split out for
file size (see core/position_monitor.py for the assembled class).

DO NOT instantiate _EntryMixin directly — it is a mixin, combined
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


class _EntryMixin:
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
        desc = ""

        for edge_name, edge in (("top", self.rect_top), ("bottom", self.rect_bottom)):
            if bid <= edge <= ask:
                touched = True
                desc = f"{edge_name} bid/ask straddle bid={bid:.5f} ask={ask:.5f}"
                break
            if self._prev_tick_price is not None:
                prev = self._prev_tick_price
                if (prev < edge <= mid) or (mid <= edge < prev):
                    touched = True
                    desc = f"{edge_name} crossed {prev:.5f}→{mid:.5f}"
                    break

        self._prev_tick_price = mid

        if touched:
            height = self.rect_top - self.rect_bottom
            min_required = self._min_required_height()
            if min_required > 0 and height < min_required:
                if not self._rect_too_small_warned:
                    self._rect_too_small_warned = True
                    self._log(
                        f"🚫  [{self.name[:20]}] rectangle is {height/self.pip_size:.1f} pips "
                        f"tall, but this broker requires at least "
                        f"{min_required/self.pip_size:.1f} pips between entry and SL — "
                        f"every order would be rejected. Redraw a taller rectangle for "
                        f"this symbol. (Not retrying this one again.)", "ERROR"
                    )
                return False
            self._log(
                f"🎯  [{self.name[:20]}] touched ({desc}) | "
                f"rect=[{self.rect_bottom:.5f}-{self.rect_top:.5f}] | placing orders", "NEW"
            )

            # ── OB+FVG entry filter ──────────────────────────────
            if getattr(self, "_entry_filter_ob_fvg", False):
                import config as _cfg
                # Determine which edge was touched and expected direction
                touched_bottom = "bottom" in desc
                direction = "BULL" if touched_bottom else "BEAR"
                edge_price = self.rect_bottom if touched_bottom else self.rect_top
                from core.entry_filter import check_ob_fvg_confluence_at_edge
                allowed, zone, filter_msg = check_ob_fvg_confluence_at_edge(
                    symbol       = self.symbol,
                    pip_size     = self.pip_size,
                    edge_price   = edge_price,
                    direction    = direction,
                    overlap_pips = getattr(_cfg, "ENTRY_FILTER_OVERLAP_PIPS", 15.0),
                    min_score    = getattr(_cfg, "ENTRY_FILTER_MIN_SCORE", 10.0),
                )
                self._log(filter_msg, "NEW" if allowed else "WARN")
                if not allowed:
                    # Reset to IDLE — wait for the next touch
                    return False

            self.place_initial_pair()
            return True

        return False

    def place_initial_pair(self):
        if self.round == 0:
            # ── Check for a saved session for this rectangle ──────
            # Even when resume_enabled=False, the session file records
            # touch_count/round/lots/cumulative_loss after every loss.
            # If the bot was restarted mid-cycle (crash, manual stop,
            # PC reboot) we MUST restore that progress so the next
            # touch uses the correct lot — not restart from 0.01.
            # This is different from full resume (which restores live
            # MT5 positions too); here we only restore the counters
            # so place_initial_pair places the right lot on a clean
            # first touch of the rectangle after restart.
            restored = self._try_restore_session_counters()
            if not restored:
                # Truly fresh — no prior session for this rectangle.
                self.round = 1
                self.touch_count = 0
                self.buy_lot = self.base_lot
                self.sell_lot = self.base_lot
        # else: round > 0 means this is a RETRY after a gap-correction
        # (_abort_gapped_round — see position_protection.py) mid-cycle,
        # not a fresh start. Deliberately do NOT reset round/touch_count/
        # buy_lot/sell_lot here — the whole point of that retry path is
        # to resume exactly where the gap-corrected round left off
        # (same lot, same round, same cumulative_loss), not restart the
        # recovery cycle from scratch just because one fill needed
        # correcting. The rectangle's edges never move either way, so
        # _buy_entry/_sell_entry below are correct for both cases.

        orders = [
            {"type": "BUY_STOP",  "entry": self._buy_entry,  "sl": self._buy_sl_price,
             "tp": self._buy_tp_price, "lot": self.buy_lot,  "source": self.rect_top, "round": 1},
            {"type": "SELL_STOP", "entry": self._sell_entry, "sl": self._sell_sl_price,
             "tp": self._sell_tp_price, "lot": self.sell_lot, "source": self.rect_bottom, "round": 1},
        ]
        results = send_pair(orders, self.symbol)

        self.buy_ticket = None
        self.sell_ticket = None
        for r in results:
            if r["ok"]:
                if r["order"]["type"] == "BUY_STOP":
                    self.buy_ticket = r["ticket"]
                    self.buy_sl = self._buy_sl_price
                else:
                    self.sell_ticket = r["ticket"]
                    self.sell_sl = self._sell_sl_price

        # ── Both legs placed: normal success path ──────────────────
        if self.buy_ticket and self.sell_ticket:
            self.state = self.PENDING
            self._log(
                f"📌  [{self.name[:20]}] R{self.round} pair placed | "
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
                    self.sell_sl = self._sell_sl_price
                else:
                    self.buy_ticket = ok[0]["ticket"]
                    self.buy_sl = self._buy_sl_price
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
            f"resetting to IDLE (will retry if touched again — nothing "
            f"actually opened, so this isn't a finished cycle)", "ERROR"
        )
        if self.buy_ticket:
            cancel_order(self.buy_ticket)
        if self.sell_ticket:
            cancel_order(self.sell_ticket)
        self.reset(final=False)