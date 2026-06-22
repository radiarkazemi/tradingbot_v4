"""
position_monitor.py — TraderBot v4

SourceState is split across several files for size/readability — this
is the file that actually assembles it via mixins, and the ONLY file
anything outside core/position_*.py should ever import from:
    from core.position_monitor import SourceState

Split layout:
  core/position_geometry.py    entry/SL/TP/R-distance calculations
  core/position_entry.py       touch detection + initial pair placement
  core/position_helpers.py     low-level broker order/position helpers
  core/position_protection.py  R1/R2/R3 + kill switches
  core/position_recovery.py    new-stop placement after a loss
  core/position_monitor.py     (this file) __init__, leg activation/
                                monitoring, reset, summary — the core
                                state machine + everything that combines
                                the mixins above into one class.

WHY _check_activation/_check_legs/__init__/reset/summary stay HERE
instead of moving to a mixin: backtest/engine.py patches
`core.position_monitor._time` directly (a module-level name, not a
class attribute) to make the post-activation grace period
(ACTIVATION_GRACE_SEC) respect simulated time instead of the real
wall clock during a backtest — see that file's docstring. That patch
only affects code that's textually IN THIS MODULE when it resolves
`_time` at call time; moving the grace-period logic to a separate
mixin module would silently break that patch (each module has its own
import namespace). Every method here that touches `_time` for that
reason stays in this file specifically.

Mixin methods freely call self.<method>() defined in OTHER mixins —
that's safe and intentional: once combined via multiple inheritance,
every method lives on the same class namespace regardless of which
file originally defined it.
"""
import logging
import time as _time
import MetaTrader5 as mt5
from config import MAGIC_NUMBER
from core.order_manager import send_pair, cancel_order, _filling_mode, _round_price
import config as cfg

from core.position_monitor_base import log, ACTIVATION_GRACE_SEC, _save
from core.position_geometry import _GeometryMixin
from core.position_entry import _EntryMixin
from core.position_helpers import _HelpersMixin
from core.position_protection import _ProtectionMixin
from core.position_recovery import _RecoveryMixin


class SourceState(_GeometryMixin, _EntryMixin, _HelpersMixin,
                  _ProtectionMixin, _RecoveryMixin):
    IDLE = "idle"
    PENDING = "pending"
    ACTIVE = "active"
    EXHAUSTED = "exhausted"

    def __init__(self, name, rect_top, rect_bottom, pip_size, symbol, base_lot,
                 start_balance=0.0, log_fn=None, stop_fn=None, kill_fn=None,
                 risk_free_enabled=False, loss_free_enabled=False,
                 soft_lot_mode=1):
        self.name = name
        # Fixed rectangle edges — NEVER move after registration. This
        # is the "distance" now: rect_top - rect_bottom. (item 5/11)
        self.rect_top = max(rect_top, rect_bottom)
        self.rect_bottom = min(rect_top, rect_bottom)
        self.pip_size = pip_size
        self.symbol = symbol
        self.base_lot = base_lot
        self.start_balance = start_balance
        self._log = log_fn or (lambda msg, level="INFO": log.info(msg))
        self._stop_fn = stop_fn
        self._kill_fn = kill_fn or stop_fn
        self._risk_free_enabled = risk_free_enabled
        self._loss_free_enabled = loss_free_enabled
        self.soft_lot_mode = soft_lot_mode if soft_lot_mode in (1, 2, 3) else 1

        self.state = self.IDLE
        self.round = 0

        self.buy_ticket = None
        self.sell_ticket = None
        self.buy_pos_ticket = None
        self.sell_pos_ticket = None

        self.buy_lot = base_lot
        self.sell_lot = base_lot
        self.buy_sl = None
        self.sell_sl = None
        self.buy_r_frozen = 0.0
        self.sell_r_frozen = 0.0

        self._buy_confirmed = False
        self._sell_confirmed = False
        self._activated_at = 0.0
        self._last_bid = 0.0
        self._last_ask = 0.0

        self.registered_at = 0
        self.last_prev_t = 0

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
        self.needs_full_reset = False   # watcher checks/clears this

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
        # log the broker min-stop-distance warning once, not every touch
        self._rect_too_small_warned = False

        self._log(
            f"⚙️  [{self.name[:20]}] mode={self.soft_lot_mode} "
            f"loss_free={self._loss_free_enabled} risk_free={self._risk_free_enabled} "
            f"rect=[{self.rect_bottom:.5f}-{self.rect_top:.5f}]",
            "INFO"
        )

    # ── Fixed price properties (never drift) ──────────────────────

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

    def _check_activation(self):
        pending = {o.ticket for o in (mt5.orders_get(symbol=self.symbol) or [])
                   if o.magic == MAGIC_NUMBER}
        positions = mt5.positions_get(symbol=self.symbol) or []
        bot_pos = [p for p in positions if p.magic == MAGIC_NUMBER]
        buy_pos = [p for p in bot_pos if p.type == 0]
        sell_pos = [p for p in bot_pos if p.type == 1]

        buy_still = self.buy_ticket in pending if self.buy_ticket else False
        sell_still = self.sell_ticket in pending if self.sell_ticket else False
        buy_filled = self.buy_ticket is not None and not buy_still
        sell_filled = self.sell_ticket is not None and not sell_still

        if not buy_filled and not sell_filled:
            return

        self._activated_at = _time.time()
        self._buy_confirmed = False
        self._sell_confirmed = False

        if buy_filled:
            if buy_pos:
                pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
                self.buy_pos_ticket = pos.ticket
                self.buy_sl = pos.sl
                self.buy_r_frozen = abs(pos.price_open - pos.sl)
                self.buy_lot = pos.volume
                self._buy_confirmed = True
            self.buy_ticket = None

        if sell_filled:
            if sell_pos:
                pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
                self.sell_pos_ticket = pos.ticket
                self.sell_sl = pos.sl
                self.sell_r_frozen = abs(pos.price_open - pos.sl)
                self.sell_lot = pos.volume
                self._sell_confirmed = True
            self.sell_ticket = None

        if buy_filled and not sell_filled:
            if self.soft_lot_mode == 3:
                next_lot = self._next_table_lot(base_lot=self.buy_lot)
                if next_lot is None:
                    return  # kill switch tripped
                self.sell_lot = next_lot
                if self.sell_ticket:
                    self._modify_order_lot(self.sell_ticket, self.sell_lot,
                                           exact_sl=self._sell_sl_price)
            # Modes 1/2: the still-pending SELL leg already carries the
            # SAME lot this round was placed with (see place_initial_pair/
            # _place_new_*_stop) — one table step per ROUND, not per
            # activation event, per item 3/6. Nothing to bump here.
            self._log(
                f"🟢  [{self.name[:20]}] R{self.round} BUY activated | "
                f"pos#{self.buy_pos_ticket} sl={self._buy_sl_price:.5f} | "
                f"SELL#{self.sell_ticket} lot (touch {self.touch_count}) → {self.sell_lot:.2f}", "NEW"
            )
        elif sell_filled and not buy_filled:
            if self.soft_lot_mode == 3:
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
            # Modes 1/2: see comment above — nothing to bump.
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
        now = _time.time()
        in_grace = (now - self._activated_at) < ACTIVATION_GRACE_SEC

        positions = mt5.positions_get(symbol=self.symbol) or []
        bot_pos = [p for p in positions if p.magic == MAGIC_NUMBER]
        open_tickets = {p.ticket for p in bot_pos}
        buy_pos = [p for p in bot_pos if p.type == 0]
        sell_pos = [p for p in bot_pos if p.type == 1]

        pending = {o.ticket for o in (mt5.orders_get(symbol=self.symbol) or [])
                   if o.magic == MAGIC_NUMBER}

        # ── Confirm unconfirmed positions ─────────────────────────
        if not self._buy_confirmed and self.buy_pos_ticket is None:
            if buy_pos:
                pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
                self.buy_pos_ticket = pos.ticket
                self.buy_sl = pos.sl
                self.buy_r_frozen = abs(pos.price_open - pos.sl)
                self.buy_lot = pos.volume
                self._buy_confirmed = True
                self._log(
                    f"🔍  [{self.name[:20]}] BUY pos confirmed "
                    f"#{pos.ticket} sl={pos.sl}", "INFO"
                )

        if not self._sell_confirmed and self.sell_pos_ticket is None:
            if sell_pos:
                pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
                self.sell_pos_ticket = pos.ticket
                self.sell_sl = pos.sl
                self.sell_r_frozen = abs(pos.price_open - pos.sl)
                self.sell_lot = pos.volume
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
                self.buy_sl = pos.sl
                self.buy_r_frozen = abs(pos.price_open - pos.sl)
                self.buy_lot = pos.volume
                self._buy_confirmed = True
                self.buy_ticket = None
                if self.soft_lot_mode == 3:
                    next_lot = self._next_table_lot(base_lot=self.buy_lot)
                    if next_lot is None:
                        return  # kill switch tripped
                    self.sell_lot = next_lot
                    if self.sell_ticket and self.sell_ticket in pending:
                        self._modify_order_lot(self.sell_ticket, self.sell_lot,
                                               exact_sl=self._sell_sl_price)
                # Modes 1/2: SELL already carries this round's lot from
                # placement — see item 3/6, nothing to bump.
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
                self.sell_sl = pos.sl
                self.sell_r_frozen = abs(pos.price_open - pos.sl)
                self.sell_lot = pos.volume
                self._sell_confirmed = True
                self.sell_ticket = None
                if self.soft_lot_mode == 3:
                    next_lot = self._next_table_lot(base_lot=self.sell_lot)
                    if next_lot is None:
                        return  # kill switch tripped
                    self.buy_lot = next_lot
                    if self.buy_ticket and self.buy_ticket in pending:
                        self._modify_order_lot(self.buy_ticket, self.buy_lot,
                                               exact_sl=self._buy_sl_price)
                # Modes 1/2: BUY already carries this round's lot from
                # placement — see item 3/6, nothing to bump.
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
        has drifted from the rectangle's fixed edge by even a single
        point — the rectangle is the permanent source of truth (see
        _buy_sl_price/_sell_sl_price), so if a broker-side requote,
        partial modification, or anything else nudges an SL away from
        its exact edge, this snaps it straight back every scan. This
        is what guarantees BUY's SL and SELL's SL are always exactly
        the two rectangle edges, continuously, not just at the moment
        each was first set.

        Skipped for a side once R1 (loss-free) or R2 (risk-free) has
        been applied to it — from that point its SL represents a
        locked-in profit level, owned exclusively by
        _check_loss_free / _check_risk_free.
        """
        TOLERANCE = 1e-6  # smaller than 1 point on any normal FX/metal digit count
        if (buy_pos and not self.risk_free_applied.get("buy", False)
                and not self.loss_free_applied.get("buy", False)):
            pos = sorted(buy_pos, key=lambda p: p.time, reverse=True)[0]
            target_sl = self._buy_sl_price
            if abs(pos.sl - target_sl) > TOLERANCE:
                self._move_position_sl(pos.ticket, target_sl)

        if (sell_pos and not self.risk_free_applied.get("sell", False)
                and not self.loss_free_applied.get("sell", False)):
            pos = sorted(sell_pos, key=lambda p: p.time, reverse=True)[0]
            target_sl = self._sell_sl_price
            if abs(pos.sl - target_sl) > TOLERANCE:
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

    def reset(self, final: bool = True):
        """
        Clear all live order/position bookkeeping for this rectangle.

        final=True (the default, and what every win/terminal-lock/
        give-up call site uses): the rectangle is RETIRED — state
        goes to EXHAUSTED, not IDLE, so it will never re-trigger on a
        future touch of the same lines. By explicit request: once a
        cycle finishes (TP win, R1/R2 terminal lock-close, margin
        exhaustion, or a bounce-confluence decline), that rectangle is
        done. The trader draws the next one themselves to start a new
        cycle — there is no auto-relocate (item 2) AND no auto-reuse
        of a rectangle that already ran its course.

        final=False: used only when both legs failed to even PLACE
        (no position or pending order was ever opened — see the
        MAX_RETRIES path in place_initial_pair) — a genuinely
        transient failure (e.g. a momentary connection hiccup) where
        retrying on a future touch of the same rectangle is still
        reasonable, since nothing about this cycle actually started
        or finished yet.
        """
        for ticket in [self.buy_ticket, self.sell_ticket]:
            if ticket:
                cancel_order(ticket)
        self.buy_ticket = None
        self.sell_ticket = None
        self.buy_pos_ticket = None
        self.sell_pos_ticket = None
        self.buy_lot = self.base_lot
        self.sell_lot = self.base_lot
        self.buy_sl = None
        self.sell_sl = None
        self.buy_r_frozen = 0.0
        self.sell_r_frozen = 0.0
        self.round = 0
        self.touch_count = 0
        self.state = self.EXHAUSTED if final else self.IDLE
        self._buy_confirmed = False
        self._sell_confirmed = False
        self.risk_free_applied = {"buy": False, "sell": False}
        self.loss_free_applied = {"buy": False, "sell": False}
        self.override_r1_price = {"buy": None, "sell": None}
        self.override_r2_price = {"buy": None, "sell": None}
        self.cumulative_loss = 0.0
        self._pip_value_per_base_lot = 0.0
        self._log(
            f"🔄  [{self.name[:20]}] state reset to "
            f"{'EXHAUSTED (rectangle retired)' if final else 'IDLE (will retry on next touch)'}"
        )
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
