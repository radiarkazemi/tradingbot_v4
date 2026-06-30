"""
core/position_helpers.py — part of core/position_monitor.SourceState, split out for
file size (see core/position_monitor.py for the assembled class).

DO NOT instantiate _HelpersMixin directly — it is a mixin, combined
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


class _HelpersMixin:
    def _close_position_by_ticket(self, ticket: int) -> bool:
        """
        Close ONE specific open position at current market price.
        Unlike _close_all_and_stop (account-wide, used by kill
        switches), this only touches the exact ticket given — used by
        the entry-gap correction (see position_protection.py's
        _check_entry_gap) to close just the one gap-filled leg (and
        its paired sibling) without disturbing any other rectangle's
        positions on the same symbol.
        """
        try:
            pos = next((p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == ticket), None)
            if not pos:
                return False
            tick = mt5.symbol_info_tick(self.symbol)
            if not tick:
                return False
            is_buy = pos.type == 0
            res = mt5.order_send({
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       self.symbol,
                "volume":       pos.volume,
                "type":         mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "position":     ticket,
                "price":        tick.bid if is_buy else tick.ask,
                "deviation":    30,
                "magic":        MAGIC_NUMBER,
                "comment":      "TB4_GapFix",
                "type_filling": _filling_mode(self.symbol),
            })
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self._log(f"✅  [{self.name[:20]}] closed #{ticket} (gap correction)", "NEW")
                return True
            self._log(
                f"⚠️  [{self.name[:20]}] failed to close #{ticket} for gap correction: "
                f"{getattr(res, 'comment', 'unknown error')}", "WARN"
            )
            return False
        except Exception as e:
            log.warning("Gap-correction close error: %s", e)
            return False

    def _move_position_sl(self, ticket: int, new_sl: float) -> bool:
        """
        Modify an open position's SL via TRADE_ACTION_SLTP.

        Handles MT5 retcode 10025 ("No changes") gracefully:
        MT5 sometimes applies the SL change but returns 10025 instead
        of 10009 (DONE) when it normalises the price to the same value
        it already has, or due to a broker-side quirk. In that case we
        re-read the position and treat it as success if pos.sl already
        matches new_sl within 1 point.
        """
        try:
            pos = next((p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == ticket), None)
            if not pos:
                return False

            # Skip if SL is already at the target (within 1 point)
            digits = getattr(mt5.symbol_info(self.symbol), "digits", 5)
            tolerance = 10 ** -digits
            if abs(pos.sl - new_sl) < tolerance:
                return True  # already at target — treat as success silently

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

            # retcode 10025 = "No changes" — MT5 may have applied it anyway.
            # Re-read the position and verify.
            NO_CHANGES = 10025
            if res and res.retcode == NO_CHANGES:
                import time as _t
                _t.sleep(0.1)
                pos2 = next((p for p in (mt5.positions_get(symbol=self.symbol) or [])
                             if p.ticket == ticket), None)
                if pos2 and abs(pos2.sl - new_sl) < tolerance:
                    return True  # MT5 applied it silently
                # Genuinely no change — SL didn't move
                return False

            self._log(
                f"⚠️  [{self.name[:20]}] SL move failed for #{ticket}: "
                f"{getattr(res, 'comment', res.retcode if res else 'no response')}",
                "WARN"
            )
            return False
        except Exception as e:
            log.warning("SL move error: %s", e)
            return False

    def _move_position_tp(self, ticket: int, new_tp: float) -> bool:
        """
        Modify an open position's TP via TRADE_ACTION_SLTP, keeping
        its current SL untouched.

        Handles MT5 retcode 10025 ("No changes") gracefully, the same
        way _move_position_sl does: if the position's TP already
        matches new_tp, treat it as success rather than logging a
        spurious "resync failed" warning every scan.
        """
        try:
            pos = next((p for p in (mt5.positions_get(symbol=self.symbol) or [])
                       if p.ticket == ticket), None)
            if not pos:
                return False

            digits    = getattr(mt5.symbol_info(self.symbol), "digits", 5)
            tolerance = 10 ** -digits
            if abs(pos.tp - new_tp) < tolerance:
                return True  # already at target

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

            if res and res.retcode == 10025:
                import time as _t
                _t.sleep(0.1)
                pos2 = next((p for p in (mt5.positions_get(symbol=self.symbol) or [])
                             if p.ticket == ticket), None)
                if pos2 and abs(pos2.tp - new_tp) < tolerance:
                    return True
                return False

            self._log(
                f"⚠️  [{self.name[:20]}] TP resync failed for #{ticket}: "
                f"{getattr(res, 'comment', res.retcode if res else 'no response')}",
                "WARN"
            )
            return False
        except Exception as e:
            log.warning("TP resync error: %s", e)
            return False

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
    def _try_restore_session_counters(self) -> bool:
        """
        Load touch_count, round, buy_lot, sell_lot, and cumulative_loss
        from the session file IF it matches this rectangle's edges.

        Called from place_initial_pair on a round==0 fresh start so
        that restarting the bot mid-cycle continues from the correct
        lot rather than resetting to base_lot.

        Returns True if counters were restored, False if no matching
        session file exists (caller should use fresh defaults).

        This is intentionally narrow — it ONLY restores counters, never
        live MT5 positions or order tickets. Full position/order resume
        still goes through scan_and_resume (resume_enabled flow). The
        two mechanisms are complementary: scan_and_resume handles the
        case where MT5 positions are still open; this handles the case
        where positions already closed but the rectangle is being
        re-touched on the next fresh entry after a bot restart.
        """
        try:
            from core.resume import session_file
            import json, os
            sf = session_file(self.symbol)
            if not os.path.exists(sf):
                return False
            with open(sf) as f:
                data = json.load(f)

            # Only restore if the session file is for THIS rectangle
            # (same edges within 1 pip tolerance — guards against
            # accidentally picking up a session from a different rect).
            tol = self.pip_size * 2
            saved_top    = data.get("rect_top", 0)
            saved_bottom = data.get("rect_bottom", 0)
            if (abs(saved_top - self.rect_top) > tol
                    or abs(saved_bottom - self.rect_bottom) > tol):
                return False  # different rectangle — don't restore

            # Only restore if the session had actual progress
            saved_round = int(data.get("round", 0))
            saved_touch = int(data.get("touch_count", 0))
            if saved_round <= 1 and saved_touch == 0:
                return False  # nothing meaningful to restore

            self.round         = saved_round
            self.touch_count   = saved_touch
            self.buy_lot       = float(data.get("buy_lot", self.base_lot))
            self.sell_lot      = float(data.get("sell_lot", self.base_lot))
            self.cumulative_loss = float(data.get("cumulative_loss", 0.0))
            self._log(
                f"📂  [{self.name[:20]}] session restored after restart — "
                f"R{self.round} touch={self.touch_count} "
                f"lot={self.buy_lot:.2f}/{self.sell_lot:.2f} "
                f"cumulative_loss=${self.cumulative_loss:.2f} "
                f"(continuing mid-cycle, not restarting from lot 1)",
                "NEW"
            )
            return True
        except Exception as e:
            log.warning("Session counter restore error: %s", e)
            return False