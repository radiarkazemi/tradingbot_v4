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

    # ── Activation ────────────────────────────────────────────────

    def _check_loss_free(self, buy_pos: list, sell_pos: list):
        """
        R1 — Loss-Free. Once an open position's floating profit
        reaches LOSS_FREE_TRIGGER_R (default 1R), move its SL far
        enough to cover ALL cumulative losses taken so far this
        session/cycle, PLUS this round's own risk — the exact same
        loss-covering formula R2 (risk-free) uses (see
        _risk_free_lock_distance), just triggered earlier (1R instead
        of 2R) and with no partial exit (that's exclusively R2's).

        This guarantees strictly more than plain breakeven: even in
        the worst case (the full cumulative amount isn't reachable
        yet at this profit level), _clamp_sl_to_valid_range never lets
        the result fall below the position's own entry, so "loss-free"
        is still always at least true — it just won't always cover
        100% of the session's losses if 1R alone isn't far enough yet.

        Mirrors _check_risk_free's structure exactly, one trigger
        level lower. If R2 later also fires for the same side, it
        simply overwrites the SL again (R2 owns the SL once
        triggered — see the skip guards in _resync_open_sl/tp).

        Item 9: once applied, the trader can drag the bot-drawn
        TB4_R1_<name> chart rectangle to a different price; the
        watcher reads it back each scan into
        self.override_r1_price[side], and that value is used here
        instead of recalculating, for as long as it stays != None.
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
                    if self.override_r1_price.get("buy") is not None:
                        new_sl = _round_price(
                            self.override_r1_price["buy"], self.symbol)
                    else:
                        lock_dist = self._risk_free_lock_distance(
                            r, pos.volume)
                        new_sl = _round_price(
                            pos.price_open + lock_dist, self.symbol)
                        new_sl, _clamped = self._clamp_sl_to_valid_range(
                            new_sl, pos, is_buy=True)
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.loss_free_applied["buy"] = True
                        self._log(
                            f"🟩  [{self.name[:20]}] BUY loss-free (R1) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL moved to {new_sl:.5f} "
                            f"(covers cumulative_loss=${self.cumulative_loss:.2f} "
                            f"+ this round's risk)", "NEW"
                        )

        if (sell_pos and not self.loss_free_applied.get("sell", False)
                and not self.risk_free_applied.get("sell", False)):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            r = self.sell_r_frozen
            if r > 0:
                profit_dist = pos.price_open - pos.price_current
                if profit_dist >= trigger_r * r:
                    if self.override_r1_price.get("sell") is not None:
                        new_sl = _round_price(
                            self.override_r1_price["sell"], self.symbol)
                    else:
                        lock_dist = self._risk_free_lock_distance(
                            r, pos.volume)
                        new_sl = _round_price(
                            pos.price_open - lock_dist, self.symbol)
                        new_sl, _clamped = self._clamp_sl_to_valid_range(
                            new_sl, pos, is_buy=False)
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.loss_free_applied["sell"] = True
                        self._log(
                            f"🟩  [{self.name[:20]}] SELL loss-free (R1) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL moved to {new_sl:.5f} "
                            f"(covers cumulative_loss=${self.cumulative_loss:.2f} "
                            f"+ this round's risk)", "NEW"
                        )

        # ── Trader-adjusted override, post-application ─────────────
        # Once R1 is applied, keep tracking the chart line in case the
        # trader drags it to a different price (item 9).
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
                        new_sl = _round_price(
                            self.override_r2_price["buy"], self.symbol)
                    else:
                        lock_dist = self._risk_free_lock_distance(
                            r, lot_for_lock, multiplier=2.0)
                        new_sl = _round_price(
                            pos.price_open + lock_dist, self.symbol)
                        new_sl, clamped = self._clamp_sl_to_valid_range(
                            new_sl, pos, is_buy=True)
                        if clamped:
                            self._log(
                                f"⚠️  [{self.name[:20]}] BUY risk-free lock distance "
                                f"clamped to {new_sl:.5f} — the full cumulative-loss-"
                                f"covering amount would have put SL on the wrong side "
                                f"of current price/TP (this is the fix for an earlier "
                                f"'Invalid stops' loop on small remaining lots after a "
                                f"partial exit); locking the best achievable amount "
                                f"instead of nothing", "WARN"
                            )
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.risk_free_applied["buy"] = True
                        self._log(
                            f"🛡️  [{self.name[:20]}] BUY risk-free (R2) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL moved to {new_sl:.5f} "
                            f"(2× cumulative_loss=${self.cumulative_loss:.2f} "
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
                        new_sl = _round_price(
                            self.override_r2_price["sell"], self.symbol)
                    else:
                        lock_dist = self._risk_free_lock_distance(
                            r, lot_for_lock, multiplier=2.0)
                        new_sl = _round_price(
                            pos.price_open - lock_dist, self.symbol)
                        new_sl, clamped = self._clamp_sl_to_valid_range(
                            new_sl, pos, is_buy=False)
                        if clamped:
                            self._log(
                                f"⚠️  [{self.name[:20]}] SELL risk-free lock distance "
                                f"clamped to {new_sl:.5f} — the full cumulative-loss-"
                                f"covering amount would have put SL on the wrong side "
                                f"of current price/TP (this is the fix for an earlier "
                                f"'Invalid stops' loop on small remaining lots after a "
                                f"partial exit); locking the best achievable amount "
                                f"instead of nothing", "WARN"
                            )
                    if self._move_position_sl(pos.ticket, new_sl):
                        self.risk_free_applied["sell"] = True
                        self._log(
                            f"🛡️  [{self.name[:20]}] SELL risk-free (R2) | "
                            f"profit={profit_dist:.5f} ≥ {trigger_r}R={trigger_r*r:.5f} | "
                            f"SL moved to {new_sl:.5f} "
                            f"(2× cumulative_loss=${self.cumulative_loss:.2f} "
                            f"+ this round's risk)", "NEW"
                        )

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
        already locked in on one or both sides. By request: turning
        the feature off should undo whatever lock it applied, not
        leave the SL parked at the locked level forever — restore the
        normal rectangle-pinned SL (_buy_sl_price/_sell_sl_price) and
        let _resync_open_sl resume normal operation on that side.

        Can't undo a partial close if R1 itself never does one (only
        R2/risk-free does) — only the SL itself is reverted here.
        """
        try:
            buy_pos = [p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == self.buy_pos_ticket]
            sell_pos = [p for p in (mt5.positions_get(symbol=self.symbol) or [])
                        if p.ticket == self.sell_pos_ticket]
        except Exception:
            buy_pos, sell_pos = [], []

        if self.loss_free_applied.get("buy", False):
            self.loss_free_applied["buy"] = False
            self.override_r1_price["buy"] = None
            if buy_pos:
                self._move_position_sl(buy_pos[0].ticket, self._buy_sl_price)
            self._log(
                f"🟩  [{self.name[:20]}] Loss-Free disabled — BUY SL reverted to rectangle edge")

        if self.loss_free_applied.get("sell", False):
            self.loss_free_applied["sell"] = False
            self.override_r1_price["sell"] = None
            if sell_pos:
                self._move_position_sl(sell_pos[0].ticket, self._sell_sl_price)
            self._log(
                f"🟩  [{self.name[:20]}] Loss-Free disabled — SELL SL reverted to rectangle edge")

    def revert_risk_free(self):
        """
        Called when the trader DISABLES Risk-Free (R2) while it's
        already locked in. Same idea as revert_loss_free — restores
        the normal rectangle-pinned SL. Note: if a partial exit
        already executed, that volume is gone for good (a real
        executed deal can't be un-closed) — only the SL on whatever
        volume remains gets reverted.
        """
        try:
            buy_pos = [p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == self.buy_pos_ticket]
            sell_pos = [p for p in (mt5.positions_get(symbol=self.symbol) or [])
                        if p.ticket == self.sell_pos_ticket]
        except Exception:
            buy_pos, sell_pos = [], []

        if self.risk_free_applied.get("buy", False):
            self.risk_free_applied["buy"] = False
            self.override_r2_price["buy"] = None
            if buy_pos:
                self._move_position_sl(buy_pos[0].ticket, self._buy_sl_price)
            self._log(
                f"🛡️  [{self.name[:20]}] Risk-Free disabled — BUY SL reverted to rectangle edge")

        if self.risk_free_applied.get("sell", False):
            self.risk_free_applied["sell"] = False
            self.override_r2_price["sell"] = None
            if sell_pos:
                self._move_position_sl(sell_pos[0].ticket, self._sell_sl_price)
            self._log(
                f"🛡️  [{self.name[:20]}] Risk-Free disabled — SELL SL reverted to rectangle edge")

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
