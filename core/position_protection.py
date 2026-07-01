"""
core/position_protection.py — part of core/position_monitor.SourceState, split out for
file size (see core/position_monitor.py for the assembled class).

DO NOT instantiate _ProtectionMixin directly — it is a mixin, combined
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


class _ProtectionMixin:
    def _check_entry_gap(self, pos, intended_entry: float, is_buy: bool) -> bool:
        """
        Detects a candle-gap fill (this leg filled meaningfully far
        from the rectangle edge it was supposed to enter at).

        NEW BEHAVIOUR (replacing the old close-both-legs logic):
        ─────────────────────────────────────────────────────────
        • If the position is ALREADY ACTIVE (confirmed) — the other
          leg has already been confirmed too — we leave it completely
          alone. The cycle is live; disturbing it would make things
          worse. Returns False so normal processing continues.

        • If the position just filled but the SIBLING is still a
          PENDING stop order — we modify the pending order's entry
          price to the rectangle edge (the intended entry) instead of
          cancelling it. The gapped leg itself is left running; its SL
          is already pinned to the rectangle edge by _resync_open_sl
          on every scan, which corrects any structural gap for free.
          Only the still-open pending order's price is fixed. Returns
          False so normal processing continues (no round abort).

        • If we cannot modify the pending order (broker rejection or
          order already gone), we fall back to the old behaviour and
          abort the round so the source retries cleanly.

        Returns True ONLY if an unrecoverable gap was detected and the
        round was aborted (caller MUST stop processing immediately).
        Returns False in all other cases (normal processing continues).
        """
        height = self.rect_top - self.rect_bottom
        tolerance = height * getattr(cfg, "ENTRY_GAP_TOLERANCE_FRACTION", 0.20)
        gap = abs(pos.price_open - intended_entry)
        if gap <= tolerance:
            return False  # within tolerance — normal slippage, nothing to do

        side = "BUY" if is_buy else "SELL"

        # ── Case 1: sibling already confirmed (both legs active) ───
        # The cycle is running with both positions live. Closing one
        # now would leave an unhedged position — worse than doing
        # nothing. Leave both alone; _resync_open_sl will keep each
        # SL pinned to the rectangle edge every scan regardless.
        sibling_confirmed = (
            self._sell_confirmed if is_buy else self._buy_confirmed
        )
        if sibling_confirmed:
            self._log(
                f"ℹ️  [{self.name[:20]}] {side} gap-fill detected "
                f"({gap/self.pip_size:.1f} pips, tolerance "
                f"{tolerance/self.pip_size:.1f}p) but sibling already active — "
                f"leaving both positions running, SL will self-correct via resync",
                "INFO"
            )
            return False  # do NOT abort — let normal processing continue

        # ── Case 2: sibling is still a pending stop order ──────────
        # Modify the pending order's entry price to the intended
        # (rectangle-edge) price so it fills at the correct level on
        # the next touch rather than at a gapped price.
        sibling_ticket = self.sell_ticket if is_buy else self.buy_ticket
        sibling_sl = self._sell_sl_price if is_buy else self._buy_sl_price
        sibling_entry = self._sell_entry if is_buy else self._buy_entry

        if sibling_ticket:
            self._log(
                f"⚠️  [{self.name[:20]}] {side} gap-filled at "
                f"{pos.price_open:.5f} — {gap/self.pip_size:.1f} pips from "
                f"intended {intended_entry:.5f} (tolerance "
                f"{tolerance/self.pip_size:.1f}p) — modifying sibling pending "
                f"order #{sibling_ticket} to rectangle edge (no close/reset)",
                "WARN"
            )
            # Attempt to modify the pending order's price back to the
            # rectangle edge. This is a no-op cost-wise if it was
            # already there; it fixes it if the gap shifted it.
            modified = self._modify_pending_entry(
                sibling_ticket, sibling_entry, sibling_sl)
            if modified:
                self._log(
                    f"✅  [{self.name[:20]}] sibling order #{sibling_ticket} "
                    f"entry corrected to {sibling_entry:.5f} — cycle continues",
                    "INFO"
                )
                return False  # successfully corrected — continue normally
            else:
                # Modification failed — fall through to abort
                self._log(
                    f"⚠️  [{self.name[:20]}] could not modify sibling order "
                    f"#{sibling_ticket} — falling back to round abort",
                    "WARN"
                )

        # ── Case 3: fallback abort (no sibling or modification failed)
        self._log(
            f"⚠️  [{self.name[:20]}] {side} gap-fill unrecoverable — "
            f"closing gapped leg and aborting round "
            f"(cumulative_loss=${self.cumulative_loss:.2f} preserved)", "ERROR"
        )
        self._close_position_by_ticket(pos.ticket)

        # Cancel whatever sibling state exists
        if sibling_ticket:
            cancel_order(sibling_ticket)
        sibling_pos_ticket = self.sell_pos_ticket if is_buy else self.buy_pos_ticket
        if sibling_pos_ticket and sibling_pos_ticket != pos.ticket:
            self._close_position_by_ticket(sibling_pos_ticket)

        self._abort_gapped_round()
        return True

    def _cancel_opposite_leg(self, winning_side: str):
        """
        Cancel the opposite side's pending stop order once the active
        position has been locked into guaranteed profit by R1 or R2.

        Once a position is loss-free or risk-free, the recovery
        mechanism has already "won" this round — there is no need to
        keep a pending stop order on the other side waiting to fire
        as the next recovery leg. Cancelling it:
          - Removes clutter / unintended exposure
          - Lets the winning position run cleanly to its TP (or
            trailing SL) without a recovery order looming behind it

        Args:
            winning_side: "buy" or "sell" — the side that just got
                          R1/R2 protection applied.
        """
        try:
            from core.order_manager import cancel_order
            opposite_ticket = (
                self.sell_ticket if winning_side == "buy" else self.buy_ticket
            )
            if opposite_ticket:
                if cancel_order(opposite_ticket):
                    self._log(
                        f"🗑️  [{self.name[:20]}] Cancelled opposite "
                        f"{'SELL' if winning_side == 'buy' else 'BUY'}-STOP "
                        f"#{opposite_ticket} — {winning_side.upper()} is now "
                        f"protected and running solo", "INFO"
                    )
                if winning_side == "buy":
                    self.sell_ticket = None
                else:
                    self.buy_ticket = None
        except Exception as e:
            log.warning("Cancel opposite leg error: %s", e)

    def _modify_pending_entry(self, ticket: int, new_entry: float,
                              new_sl: float) -> bool:
        """
        Modify a pending stop order's entry price (and SL) via
        TRADE_ACTION_MODIFY. Used by gap correction to nudge a
        sibling order back to the rectangle edge after the first
        leg filled with a gap, without cancelling anything.

        Handles MT5 retcode 10025 ("No changes"): if the order price
        already matches new_entry, treat it as success so the round
        is NOT aborted unnecessarily.

        Returns True on success, False on failure.
        """
        try:
            orders = mt5.orders_get(symbol=self.symbol) or []
            target = next((o for o in orders if o.ticket == ticket), None)
            if not target:
                return False  # order already gone (filled or cancelled)

            # Already at the correct price — nothing to modify
            digits    = getattr(mt5.symbol_info(self.symbol), "digits", 5)
            tolerance = 10 ** -digits
            if abs(target.price_open - new_entry) < tolerance:
                return True  # already correct, treat as success

            res = mt5.order_send({
                "action":   mt5.TRADE_ACTION_MODIFY,
                "order":    ticket,
                "price":    new_entry,
                "sl":       new_sl,
                "tp":       target.tp,
            })
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                return True

            # retcode 10025 = "No changes" — verify order was actually updated
            if res and res.retcode == 10025:
                import time as _t
                _t.sleep(0.1)
                orders2 = mt5.orders_get(symbol=self.symbol) or []
                t2 = next((o for o in orders2 if o.ticket == ticket), None)
                if t2 and abs(t2.price_open - new_entry) < tolerance:
                    return True  # applied silently
                return False    # genuine no-change, entry stayed wrong

            self._log(
                f"⚠️  [{self.name[:20]}] pending order modify failed "
                f"#{ticket}: {getattr(res, 'comment', res.retcode if res else 'no response')}",
                "WARN"
            )
            return False
        except Exception as e:
            log.warning("Pending order modify error: %s", e)
            return False

    def _abort_gapped_round(self):
        """
        Clear ONLY the per-attempt order/position bookkeeping so this
        rectangle goes back to IDLE and retries the CURRENT round on
        the next clean touch — deliberately NOT the same as
        reset(final=False), which also zeroes cumulative_loss, round,
        touch_count, and buy_lot/sell_lot. None of that should be lost
        just because one fill needed correcting; this round's progress
        (lot size, touch count, and every real loss already taken)
        carries forward unchanged into the retry.
        """
        for ticket in [self.buy_ticket, self.sell_ticket]:
            if ticket:
                cancel_order(ticket)
        self.buy_ticket = None
        self.sell_ticket = None
        self.buy_pos_ticket = None
        self.sell_pos_ticket = None
        self.buy_sl = None
        self.sell_sl = None
        self.buy_r_frozen = 0.0
        self.sell_r_frozen = 0.0
        self.state = self.IDLE
        self._buy_confirmed = False
        self._sell_confirmed = False
        self.risk_free_applied = {"buy": False, "sell": False}
        self.loss_free_applied = {"buy": False, "sell": False}
        self.override_r1_price = {"buy": None, "sell": None}
        self.override_r2_price = {"buy": None, "sell": None}
        # Deliberately UNCHANGED: cumulative_loss, round, touch_count,
        # buy_lot, sell_lot, _pip_value_per_base_lot — see docstring.
        self._log(
            f"🔄  [{self.name[:20]}] gap-corrected — back to IDLE, will retry "
            f"R{self.round} at lot={self.buy_lot:.2f}/{self.sell_lot:.2f} "
            f"on the next clean touch (cumulative_loss=${self.cumulative_loss:.2f} kept)"
        )

    def _check_balance_tp(self):
        if self.start_balance <= 0:
            return
        try:
            ratio = getattr(cfg, 'BALANCE_TP_RATIO', 1.10)
            info = mt5.account_info()
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
            info = mt5.account_info()
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
        tick = mt5.symbol_info_tick(self.symbol)

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
                self._log(
                    f"🗑️  Cleared saved start balance (session complete)", "INFO")
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

    # ── R1 — Loss-Free ────────────────────────────────────────────
    #
    # WHAT IT DOES (reworked):
    #   Once floating profit ≥ LOSS_FREE_TRIGGER_R (default 1R),
    #   move the SL to a price that, if hit, would yield exactly
    #   $0 net result for the session — i.e. the locked profit
    #   exactly covers the cumulative_loss accumulated so far.
    #
    #   SL distance from entry = cumulative_loss / dollar_per_pip
    #   expressed in price units.
    #
    #   If cumulative_loss is 0 (first round, nothing lost yet),
    #   the SL is set to the position's own entry (true breakeven).
    #
    #   This is INDEPENDENT of Risk-Free (R2) — enabling/disabling
    #   one has no effect on the other's SL calculation.
    #
    # ── R2 — Risk-Free ────────────────────────────────────────────
    #
    # WHAT IT DOES (reworked):
    #   Once floating profit ≥ RISK_FREE_TRIGGER_R (default 2R),
    #   move the SL to lock in 2× the loss-free distance:
    #
    #   SL distance = loss_free_distance × 2
    #
    #   where loss_free_distance is the same formula as R1 above.
    #   This guarantees the locked profit covers TWICE the session
    #   loss — a meaningful buffer above pure breakeven.
    #
    #   R2 can also do a partial close (PARTIAL_EXIT_ENABLED) before
    #   moving the SL, same as before.
    #
    #   Completely independent of R1: if R1 is disabled, R2 still
    #   calculates its own distance using the same base formula.
    #   If R2 fires after R1, it simply overwrites R1's SL with the
    #   larger 2× value.

    def _loss_free_lock_distance(self, lot: float) -> float:
        """
        Price distance (≥0) beyond entry for the Loss-Free (R1) SL.

        Goal: SL at a price where, if hit, the position's profit exactly
        covers any cumulative session losses → net session result = $0.

        Formula:
            distance = max(min_pip, cumulative_loss / dollar_per_pip)

        Minimum of 1 pip is enforced so the broker never normalises the
        SL to a price below or equal to the entry price (which would put
        the stop in loss territory or cause a rejection).

        If cumulative_loss = 0: distance = 1 pip (bare breakeven buffer).
        """
        # Minimum: always place SL at least 1 pip above entry
        min_dist = self.pip_size

        dpp = self._dollar_per_pip(lot)
        if dpp <= 0:
            # dpp unavailable — use calibrated value scaled to this lot
            # if that's also missing, use minimum distance only
            if getattr(self, "_pip_value_per_base_lot", 0.0) > 0 and lot > 0:
                base = getattr(self, "base_lot", 0.01) or 0.01
                dpp = self._pip_value_per_base_lot * (lot / base)
            if dpp <= 0:
                return min_dist

        loss_pips = max(0.0, self.cumulative_loss) / dpp
        return max(min_dist, loss_pips * self.pip_size)

    def _risk_free_lock_distance(self, lot: float, tp_dist: float) -> float:
        """
        Price distance (≥0) beyond entry for the Risk-Free (R2) SL.

        Design (3-step geometry):
          The entry→TP range is divided into 3 equal steps.
          R2 TRIGGERS when price reaches 2/3 of the entry→TP range.
          R2 LOCKS SL at the 2/3 mark — guaranteeing 2/3 of TP as profit.

          If session has losses, SL is raised further so that if hit,
          the profit covers cumulative_loss × 2 (recovery buffer).
          Always the MAX of (2/3 TP distance, loss×2 coverage).

        Args:
            lot:     active position lot (for dollar_per_pip calculation)
            tp_dist: entry→TP distance in price units

        Returns:
            Price distance from entry to lock the SL at.
        """
        # 2/3 of the entry→TP range — minimum 1 pip so broker
        # never normalises the SL back below/at entry
        two_thirds = max(tp_dist * (2.0 / 3.0), self.pip_size)

        # Session loss recovery: cover cumulative_loss × 2
        loss_coverage = 0.0
        if self.cumulative_loss > 0:
            dpp = self._dollar_per_pip(lot)
            if dpp <= 0 and getattr(self, "_pip_value_per_base_lot", 0.0) > 0:
                base = getattr(self, "base_lot", 0.01) or 0.01
                dpp  = self._pip_value_per_base_lot * (lot / base)
            if dpp > 0:
                loss_pips     = (self.cumulative_loss * 2.0) / dpp
                loss_coverage = loss_pips * self.pip_size

        return max(two_thirds, loss_coverage)

    def _check_loss_free(self, buy_pos: list, sell_pos: list):
        """
        R1 — Loss-Free. Completely independent of R2 (Risk-Free).

        Trigger: floating profit ≥ LOSS_FREE_TRIGGER_R × frozen_R.
        Action:  move SL to entry + _loss_free_lock_distance(lot),
                 which locks exactly enough profit to cover all
                 cumulative session losses (net result = $0).
                 If no losses yet, SL goes to entry (breakeven).

        Does NOT use _risk_free_lock_distance — the two features
        have completely separate formulas and do not call each other.

        Item 9: trader-draggable chart line override still works —
        if override_r1_price is set, that price is used as-is.
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
                    clamped = False
                    if self.override_r1_price.get("buy") is not None:
                        new_sl = _round_price(
                            self.override_r1_price["buy"], self.symbol)
                    else:
                        lock_dist = self._loss_free_lock_distance(pos.volume)
                        new_sl = _round_price(
                            pos.price_open + lock_dist, self.symbol)
                        new_sl, clamped = self._clamp_sl_to_valid_range(
                            new_sl, pos, is_buy=True)
                        if clamped:
                            self._log(
                                f"⚠️  [{self.name[:20]}] BUY loss-free SL clamped "
                                f"to {new_sl:.5f} — full session-loss coverage not yet "
                                f"reachable; locking best achievable (still ≥ breakeven)",
                                "WARN"
                            )
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.loss_free_applied["buy"] = True
                        coverage = (
                            "partial (clamped)" if clamped else
                            (f"SL at entry (breakeven)" if self.cumulative_loss <= 0 else
                             f"covers session losses ${self.cumulative_loss:.2f} → net $0")
                        )
                        self._log(
                            f"🟩  [{self.name[:20]}] BUY loss-free (R1) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL → {new_sl:.5f} ({coverage})", "NEW"
                        )
                        self._cancel_opposite_leg("buy")

        if (sell_pos and not self.loss_free_applied.get("sell", False)
                and not self.risk_free_applied.get("sell", False)):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            r = self.sell_r_frozen
            if r > 0:
                profit_dist = pos.price_open - pos.price_current
                if profit_dist >= trigger_r * r:
                    clamped = False
                    if self.override_r1_price.get("sell") is not None:
                        new_sl = _round_price(
                            self.override_r1_price["sell"], self.symbol)
                    else:
                        lock_dist = self._loss_free_lock_distance(pos.volume)
                        # SELL profits when price goes DOWN.
                        # Protective SL must go ABOVE entry (into profit zone).
                        # lock_dist > 0 means we need price to stay below
                        # entry+lock_dist to lock the recovery amount.
                        new_sl = _round_price(
                            pos.price_open + lock_dist, self.symbol)
                        new_sl, clamped = self._clamp_sl_to_valid_range(
                            new_sl, pos, is_buy=False)
                        if clamped:
                            self._log(
                                f"⚠️  [{self.name[:20]}] SELL loss-free SL clamped "
                                f"to {new_sl:.5f} — full session-loss coverage not yet "
                                f"reachable; locking best achievable (still ≥ breakeven)",
                                "WARN"
                            )
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.loss_free_applied["sell"] = True
                        coverage = (
                            "partial (clamped)" if clamped else
                            (f"SL at entry (breakeven)" if self.cumulative_loss <= 0 else
                             f"covers session losses ${self.cumulative_loss:.2f} → net $0")
                        )
                        self._log(
                            f"🟩  [{self.name[:20]}] SELL loss-free (R1) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL → {new_sl:.5f} ({coverage})", "NEW"
                        )
                        self._cancel_opposite_leg("sell")

        # ── Trader-adjusted override, post-application ─────────────
        if self.loss_free_applied.get("buy", False) and buy_pos:
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            ov = self.override_r1_price.get("buy")
            if ov is not None and abs(pos.sl - ov) > self.pip_size * 0.9:
                self._move_position_sl(
                    pos.ticket, _round_price(ov, self.symbol))

        if self.loss_free_applied.get("sell", False) and sell_pos:
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            ov = self.override_r1_price.get("sell")
            if ov is not None and abs(pos.sl - ov) > self.pip_size * 0.9:
                self._move_position_sl(
                    pos.ticket, _round_price(ov, self.symbol))

    def _check_risk_free(self, buy_pos: list, sell_pos: list):
        """
        R2 — Risk-Free SL lock. Completely independent of R1 (Loss-Free).

        Trigger: price reaches 2/3 of entry→TP range.

        Action: Move SL to the 2/3 mark — locks 2/3 of TP as minimum
        profit. If session has cumulative losses, SL is raised further
        to cover losses × 2.

        Partial exit is now separate (R3) — see _check_partial_exit_r3().
        """
        if not self._risk_free_enabled:
            return
        trigger_r = getattr(cfg, "RISK_FREE_TRIGGER_R", 2.0)

        if buy_pos and not self.risk_free_applied.get("buy", False):
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            # Use ONLY the FROZEN tp_dist, set the first time
            # _resync_open_tp establishes a real TP after fill.
            # NO live pos.tp fallback here — that was the cause of
            # R2 firing with a near-zero distance right after fill,
            # locking the SL "right behind" entry instead of at the
            # correct 2/3 mark. We simply wait one more scan cycle
            # (a few hundred ms) until the freeze is set properly.
            tp_dist = getattr(self, "buy_tp_frozen", 0.0)
            if tp_dist > 0:
                profit_dist  = pos.price_current - pos.price_open
                trigger_dist = tp_dist * (2.0 / 3.0)
                if profit_dist >= trigger_dist:
                    lot_for_lock = pos.volume
                    clamped  = False
                    lock_dist = 0.0
                    if self.override_r2_price.get("buy") is not None:
                        new_sl    = _round_price(self.override_r2_price["buy"], self.symbol)
                        lock_dist = new_sl - pos.price_open
                    else:
                        lock_dist = self._risk_free_lock_distance(lot_for_lock, tp_dist)
                        new_sl    = _round_price(pos.price_open + lock_dist, self.symbol)
                    new_sl, clamped = self._clamp_sl_to_valid_range(
                        new_sl, pos, is_buy=True)
                    if clamped:
                        self._log(
                            f"⚠️  [{self.name[:20]}] BUY risk-free SL clamped "
                            f"to {new_sl:.5f} — 2/3 TP level unreachable; "
                            f"locking best achievable", "WARN"
                        )
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.risk_free_applied["buy"] = True
                        locked_pips = lock_dist / self.pip_size
                        locked_usd  = locked_pips * self._dollar_per_pip(lot_for_lock)
                        pct         = profit_dist / tp_dist * 100
                        loss_note   = (
                            f" | covers loss×2=${self.cumulative_loss*2:.2f}"
                            if self.cumulative_loss > 0 else ""
                        )
                        self._log(
                            f"🛡️  [{self.name[:20]}] BUY risk-free (R2) | "
                            f"price at {pct:.0f}% of TP range ≥ 67% trigger | "
                            f"SL → {new_sl:.5f} "
                            f"(locks ≈${locked_usd:.2f} / {locked_pips:.1f}pips{loss_note})",
                            "NEW"
                        )
                        self._cancel_opposite_leg("buy")

        if sell_pos and not self.risk_free_applied.get("sell", False):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            # Use ONLY the FROZEN tp_dist — see BUY side comment above.
            tp_dist = getattr(self, "sell_tp_frozen", 0.0)
            if tp_dist > 0:
                profit_dist  = pos.price_open - pos.price_current
                trigger_dist = tp_dist * (2.0 / 3.0)
                if profit_dist >= trigger_dist:
                    lot_for_lock = pos.volume
                    clamped  = False
                    lock_dist = 0.0
                    if self.override_r2_price.get("sell") is not None:
                        new_sl    = _round_price(self.override_r2_price["sell"], self.symbol)
                        lock_dist = pos.price_open - new_sl
                    else:
                        lock_dist = self._risk_free_lock_distance(lot_for_lock, tp_dist)
                        # SELL: SL goes ABOVE entry to lock profit
                        new_sl    = _round_price(pos.price_open + lock_dist, self.symbol)
                    new_sl, clamped = self._clamp_sl_to_valid_range(
                        new_sl, pos, is_buy=False)
                    if clamped:
                        self._log(
                            f"⚠️  [{self.name[:20]}] SELL risk-free SL clamped "
                            f"to {new_sl:.5f} — 2/3 TP level unreachable; "
                            f"locking best achievable", "WARN"
                        )
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.risk_free_applied["sell"] = True
                        locked_pips = lock_dist / self.pip_size
                        locked_usd  = locked_pips * self._dollar_per_pip(lot_for_lock)
                        pct         = profit_dist / tp_dist * 100
                        loss_note   = (
                            f" | covers loss×2=${self.cumulative_loss*2:.2f}"
                            if self.cumulative_loss > 0 else ""
                        )
                        self._log(
                            f"🛡️  [{self.name[:20]}] SELL risk-free (R2) | "
                            f"price at {pct:.0f}% of TP range ≥ 67% trigger | "
                            f"SL → {new_sl:.5f} "
                            f"(locks ≈${locked_usd:.2f} / {locked_pips:.1f}pips{loss_note})",
                            "NEW"
                        )
                        self._cancel_opposite_leg("sell")

        # ── Trader-adjusted override, post-application ─────────────
        if self.risk_free_applied.get("buy", False) and buy_pos:
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            ov = self.override_r2_price.get("buy")
            if ov is not None and abs(pos.sl - ov) > self.pip_size * 0.9:
                self._move_position_sl(
                    pos.ticket, _round_price(ov, self.symbol))

        if self.risk_free_applied.get("sell", False) and sell_pos:
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            ov = self.override_r2_price.get("sell")
            if ov is not None and abs(pos.sl - ov) > self.pip_size * 0.9:
                self._move_position_sl(
                    pos.ticket, _round_price(ov, self.symbol))

    def revert_loss_free(self):
        """
        Called when the trader DISABLES Loss-Free (R1) while it's
        already locked in on one or both sides. Restore the normal
        rectangle-pinned SL (_buy_sl_price/_sell_sl_price) and let
        _resync_open_sl resume normal operation on that side.

        LAYERING: if R2 (risk-free) has ALSO applied on a side,
        disabling R1 must NOT blow that away. Only clear R1's own
        bookkeeping in that case and leave the SL where R2 put it.
        """
        try:
            buy_pos = [p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == self.buy_pos_ticket]
            sell_pos = [p for p in (mt5.positions_get(symbol=self.symbol) or [])
                        if p.ticket == self.sell_pos_ticket]
        except Exception:
            buy_pos, sell_pos = [], []

        try:
            if self.loss_free_applied.get("buy", False):
                self.loss_free_applied["buy"] = False
                self.override_r1_price["buy"] = None
                if self.risk_free_applied.get("buy", False):
                    self._log(f"🟩  [{self.name[:20]}] Loss-Free disabled — BUY left alone "
                              f"(Risk-Free is already in control of this side's SL)")
                elif buy_pos:
                    self._move_position_sl(
                        buy_pos[0].ticket, self._buy_sl_price)
                    self._log(
                        f"🟩  [{self.name[:20]}] Loss-Free disabled — BUY SL reverted to rectangle edge")
                else:
                    self._log(f"⚠️  [{self.name[:20]}] Loss-Free disabled — BUY flag cleared but no "
                              f"open position found for ticket {self.buy_pos_ticket} (already closed?)", "WARN")
        except Exception as e:
            self._log(f"💥  [{self.name[:20]}] Loss-Free revert (BUY) crashed: "
                      f"{type(e).__name__}: {e}", "ERROR")

        try:
            if self.loss_free_applied.get("sell", False):
                self.loss_free_applied["sell"] = False
                self.override_r1_price["sell"] = None
                if self.risk_free_applied.get("sell", False):
                    self._log(f"🟩  [{self.name[:20]}] Loss-Free disabled — SELL left alone "
                              f"(Risk-Free is already in control of this side's SL)")
                elif sell_pos:
                    self._move_position_sl(
                        sell_pos[0].ticket, self._sell_sl_price)
                    self._log(
                        f"🟩  [{self.name[:20]}] Loss-Free disabled — SELL SL reverted to rectangle edge")
                else:
                    self._log(f"⚠️  [{self.name[:20]}] Loss-Free disabled — SELL flag cleared but no "
                              f"open position found for ticket {self.sell_pos_ticket} (already closed?)", "WARN")
        except Exception as e:
            self._log(f"💥  [{self.name[:20]}] Loss-Free revert (SELL) crashed: "
                      f"{type(e).__name__}: {e}", "ERROR")

    def revert_risk_free(self):
        """
        Called when the trader DISABLES Risk-Free (R2) while it's
        already locked in. Same idea as revert_loss_free.

        LAYERING: if R1 (loss-free) is STILL enabled and was also
        applied on this side before R2 superseded it, disabling R2
        falls back to R1's lock (loss-free distance, not 2×) — NOT
        back to the raw rectangle edge.
        """
        try:
            buy_pos = [p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == self.buy_pos_ticket]
            sell_pos = [p for p in (mt5.positions_get(symbol=self.symbol) or [])
                        if p.ticket == self.sell_pos_ticket]
        except Exception:
            buy_pos, sell_pos = [], []

        try:
            if self.risk_free_applied.get("buy", False):
                self.risk_free_applied["buy"] = False
                self.override_r2_price["buy"] = None
                if buy_pos:
                    pos = buy_pos[0]
                    if self._loss_free_enabled and self.loss_free_applied.get("buy", False):
                        # Fall back to R1 (loss-free) level = entry + session-loss coverage
                        lock_dist = self._loss_free_lock_distance(pos.volume)
                        fallback_sl, _c = self._clamp_sl_to_valid_range(
                            _round_price(pos.price_open + lock_dist, self.symbol),
                            pos, is_buy=True)
                        self._move_position_sl(pos.ticket, fallback_sl)
                        self._log(f"🛡️  [{self.name[:20]}] Risk-Free disabled — BUY SL fell back "
                                  f"to Loss-Free level at {fallback_sl:.5f} (entry breakeven)")
                    else:
                        self._move_position_sl(pos.ticket, self._buy_sl_price)
                        self._log(
                            f"🛡️  [{self.name[:20]}] Risk-Free disabled — BUY SL reverted to rectangle edge")
                else:
                    self._log(f"⚠️  [{self.name[:20]}] Risk-Free disabled — BUY flag cleared but no "
                              f"open position found for ticket {self.buy_pos_ticket} (already closed?)", "WARN")
        except Exception as e:
            self._log(f"💥  [{self.name[:20]}] Risk-Free revert (BUY) crashed: "
                      f"{type(e).__name__}: {e}", "ERROR")

        try:
            if self.risk_free_applied.get("sell", False):
                self.risk_free_applied["sell"] = False
                self.override_r2_price["sell"] = None
                if sell_pos:
                    pos = sell_pos[0]
                    if self._loss_free_enabled and self.loss_free_applied.get("sell", False):
                        lock_dist = self._loss_free_lock_distance(pos.volume)
                        fallback_sl, _c = self._clamp_sl_to_valid_range(
                            _round_price(pos.price_open - lock_dist, self.symbol),
                            pos, is_buy=False)
                        self._move_position_sl(pos.ticket, fallback_sl)
                        self._log(f"🛡️  [{self.name[:20]}] Risk-Free disabled — SELL SL fell back "
                                  f"to Loss-Free level at {fallback_sl:.5f} (entry breakeven)")
                    else:
                        self._move_position_sl(pos.ticket, self._sell_sl_price)
                        self._log(
                            f"🛡️  [{self.name[:20]}] Risk-Free disabled — SELL SL reverted to rectangle edge")
                else:
                    self._log(f"⚠️  [{self.name[:20]}] Risk-Free disabled — SELL flag cleared but no "
                              f"open position found for ticket {self.sell_pos_ticket} (already closed?)", "WARN")
        except Exception as e:
            self._log(f"💥  [{self.name[:20]}] Risk-Free revert (SELL) crashed: "
                      f"{type(e).__name__}: {e}", "ERROR")

    def _check_trailing_sl(self, buy_pos: list, sell_pos: list):
        """
        Trailing SL — activates only AFTER R2 has locked the SL.

        Once R2 fires and sets the SL at the 2/3 mark, this function
        starts trailing the SL behind the price by TRAILING_STEP_PIPS.

        Rules:
          - Only active when _trailing_enabled = True (GUI toggle)
          - Only starts after risk_free_applied[side] = True
          - The SL can only MOVE IN PROFIT direction — never backwards
          - Step size: config.TRAILING_STEP_PIPS (default 5 pips)
          - The R2-locked SL is the floor — trailing never goes below it

        This means:
          - If price reverses right after R2: you still get the R2 profit
          - If price keeps going toward TP: SL follows, locking more profit
          - You don't need to reach TP to get a good result
        """
        if not getattr(self, "_trailing_enabled", False):
            return

        step = getattr(cfg, "TRAILING_STEP_PIPS", 5.0) * self.pip_size

        # BUY trailing
        if (buy_pos and self.risk_free_applied.get("buy", False)
                and not self.loss_free_applied.get("buy", False)):
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            if not getattr(self, "_trailing_buy_floor", 0.0):
                self._trailing_buy_floor = pos.sl
            r2_floor   = self._trailing_buy_floor
            desired_sl = pos.price_current - step
            if desired_sl > pos.sl + self.pip_size * 0.5 and desired_sl > r2_floor:
                new_sl = _round_price(desired_sl, self.symbol)
                if self._move_position_sl(pos.ticket, new_sl):
                    self._log(
                        f"📈  [{self.name[:20]}] BUY trailing SL → {new_sl:.5f} "
                        f"(price={pos.price_current:.5f} step={step/self.pip_size:.0f}pips)",
                        "INFO"
                    )

        # SELL trailing
        if (sell_pos and self.risk_free_applied.get("sell", False)
                and not self.loss_free_applied.get("sell", False)):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            if not getattr(self, "_trailing_sell_floor", 0.0):
                self._trailing_sell_floor = pos.sl
            r2_floor   = self._trailing_sell_floor
            desired_sl = pos.price_current + step
            if desired_sl < pos.sl - self.pip_size * 0.5 and desired_sl < r2_floor:
                new_sl = _round_price(desired_sl, self.symbol)
                if self._move_position_sl(pos.ticket, new_sl):
                    self._log(
                        f"📈  [{self.name[:20]}] SELL trailing SL → {new_sl:.5f} "
                        f"(price={pos.price_current:.5f} step={step/self.pip_size:.0f}pips)",
                        "INFO"
                    )

    def _check_partial_exit_r3(self, buy_pos: list, sell_pos: list):
        """
        R3 — Partial Exit at TP (final target).

        Trigger: price reaches TP (or within 1 pip of TP).

        Action: Close 70% of the position volume immediately.
                Keep the remaining 30% running with the same SL.
                The trade continues until SL or full TP hit.

        This is independent of R1 and R2. Controlled by
        the GUI 'Partial Exit (R3)' toggle button.
        Ratio: config.PARTIAL_EXIT_RATIO (default 70%).
        """
        if not getattr(self, "_partial_exit_r3_enabled", False):
            return
        ratio = getattr(cfg, "PARTIAL_EXIT_RATIO", 0.70)

        if buy_pos and not self.partial_exit_r3_done.get("buy", False):
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            if pos.tp and pos.tp > pos.price_open:
                dist_to_tp = pos.tp - pos.price_current
                # Trigger when within 1 pip of TP
                if dist_to_tp <= self.pip_size:
                    remain = self._partial_close_position(pos.ticket, ratio)
                    if remain is not None:
                        self.partial_exit_r3_done["buy"] = True
                        self._log(
                            f"📤  [{self.name[:20]}] R3 partial exit BUY "
                            f"#{pos.ticket} | closed {ratio*100:.0f}% "
                            f"at TP | {remain:.2f} lots continue",
                            "NEW"
                        )
                    elif remain is None:
                        # Volume too small — just let it hit TP fully
                        self.partial_exit_r3_done["buy"] = True

        if sell_pos and not self.partial_exit_r3_done.get("sell", False):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            if pos.tp and pos.tp < pos.price_open:
                dist_to_tp = pos.price_current - pos.tp
                if dist_to_tp <= self.pip_size:
                    remain = self._partial_close_position(pos.ticket, ratio)
                    if remain is not None:
                        self.partial_exit_r3_done["sell"] = True
                        self._log(
                            f"📤  [{self.name[:20]}] R3 partial exit SELL "
                            f"#{pos.ticket} | closed {ratio*100:.0f}% "
                            f"at TP | {remain:.2f} lots continue",
                            "NEW"
                        )
                    elif remain is None:
                        self.partial_exit_r3_done["sell"] = True

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

            raw_close = pos.volume * close_fraction
            close_vol = round(raw_close / step) * step
            close_vol = round(close_vol, 2)
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

            is_buy = pos.type == 0
            tick = mt5.symbol_info_tick(self.symbol)
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