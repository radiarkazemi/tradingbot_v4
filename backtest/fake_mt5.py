"""
backtest/fake_mt5.py — A drop-in replacement for the real `MetaTrader5`
module, backed by a simulated broker driven from real historical data.

WHY THIS EXISTS:
core/position_monitor.py (the actual live trading logic — entry/SL/TP
geometry, soft-lot table, R1/R2/R3, partial exit, kill switches) calls
`mt5.X(...)` directly throughout, dozens of times, with no dependency
injection. There are two ways to backtest that logic:

  1. Re-implement the strategy rules a second time in a separate
     backtest-only codepath.
  2. Make the REAL SourceState class run completely unmodified against
     fake historical data, by swapping out what `mt5` resolves to.

Option 1 guarantees the backtest and the live bot drift apart the
moment either one is edited without the other. Option 2 makes that
structurally impossible — the exact same `core/position_monitor.py`
file runs in both cases. This module implements option 2: every
function/constant the real `MetaTrader5` package exposes that
position_monitor.py / order_manager.py / resume.py actually use is
reproduced here, backed by `BacktestBroker`.

USAGE:
    import backtest.fake_mt5 as fake_mt5
    broker = fake_mt5.BacktestBroker(symbol_info=..., start_balance=100.0)
    fake_mt5.install(broker)          # patches sys.modules['MetaTrader5']
    import core.position_monitor      # now talks to the fake broker
    ...
    broker.advance(bid, ask, timestamp)   # call once per bar/tick
"""
import sys
import time as _time
import itertools
from dataclasses import dataclass, field
from types import SimpleNamespace

# ── Real MT5 numeric constants (kept identical to the real package so
# nothing in position_monitor.py/order_manager.py needs to change) ───
ORDER_TYPE_BUY          = 0
ORDER_TYPE_SELL         = 1
ORDER_TYPE_BUY_LIMIT    = 2
ORDER_TYPE_SELL_LIMIT   = 3
ORDER_TYPE_BUY_STOP     = 4
ORDER_TYPE_SELL_STOP    = 5

TRADE_ACTION_DEAL       = 1
TRADE_ACTION_PENDING    = 5
TRADE_ACTION_SLTP       = 6
TRADE_ACTION_MODIFY     = 7
TRADE_ACTION_REMOVE     = 8
TRADE_ACTION_CLOSE_BY   = 10

TRADE_RETCODE_DONE      = 10009
TRADE_RETCODE_REJECT    = 10006
TRADE_RETCODE_NO_MONEY  = 10019
TRADE_RETCODE_INVALID   = 10013

ORDER_TIME_GTC          = 0

ORDER_FILLING_FOK       = 0
ORDER_FILLING_IOC       = 1
ORDER_FILLING_RETURN    = 2

DEAL_ENTRY_IN           = 0
DEAL_ENTRY_OUT          = 1
DEAL_ENTRY_INOUT        = 2
DEAL_ENTRY_OUT_BY       = 3

DEAL_REASON_CLIENT      = 0
DEAL_REASON_MOBILE      = 1
DEAL_REASON_WEB         = 2
DEAL_REASON_EXPERT      = 3
DEAL_REASON_SL          = 4
DEAL_REASON_TP          = 5
DEAL_REASON_SO          = 6

TIMEFRAME_M1  = 1
TIMEFRAME_M5  = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1  = 60
TIMEFRAME_H4  = 240
TIMEFRAME_D1  = 1440


@dataclass
class SymbolSpec:
    """Static contract specs for the backtested symbol. Pull these
    from a REAL mt5.symbol_info() call once (see data_loader.py) so
    margin/profit math matches the real broker exactly — never
    hand-guess these for a live-money-adjacent tool."""
    symbol: str
    digits: int = 5
    point: float = 0.00001
    trade_tick_size: float = 0.00001
    trade_tick_value: float = 1.0
    trade_contract_size: float = 100000.0
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 100.0
    trade_stops_level: float = 0.0
    filling_mode: int = 4          # bit for ORDER_FILLING_RETURN, matches real default on most brokers
    leverage: int = 100
    spread_points: float = 10.0    # fallback fixed spread if real bid/ask not both available


class _Result:
    """Mimics the OrderSendResult namedtuple-ish object."""
    def __init__(self, retcode, order=0, comment=""):
        self.retcode = retcode
        self.order = order
        self.deal = order
        self.comment = comment


class BacktestBroker:
    """
    The simulated account + order book. Time is advanced explicitly
    by the backtest engine calling `advance(bid, ask, ts)` once per
    historical bar/tick — nothing here runs on a wall-clock thread.
    """

    def __init__(self, spec: SymbolSpec, start_balance: float, magic: int):
        self.spec = spec
        self.symbol = spec.symbol
        self.magic = magic
        self.balance = start_balance
        self.start_balance = start_balance
        self.bid = 0.0
        self.ask = 0.0
        self.now = 0.0

        self._ticket_seq = itertools.count(1)
        self.positions: dict[int, SimpleNamespace] = {}
        self.pending: dict[int, SimpleNamespace] = {}
        self.deals: list = []          # flat history, newest last
        self.equity_curve: list = []   # (timestamp, equity) sampled each advance()
        self.events: list = []         # human-readable log of every fill/close, for the report

        self.halted = False            # set True once a kill switch / R3 closes everything

    # ── Helpers ──────────────────────────────────────────────────
    def _next_ticket(self):
        return next(self._ticket_seq)

    def _profit(self, is_buy: bool, lot: float, open_price: float, close_price: float) -> float:
        diff = (close_price - open_price) if is_buy else (open_price - close_price)
        ticks = diff / self.spec.trade_tick_size
        return ticks * self.spec.trade_tick_value * lot

    def _margin(self, lot: float, price: float) -> float:
        return (lot * self.spec.trade_contract_size * price) / max(self.spec.leverage, 1)

    def equity(self) -> float:
        floating = 0.0
        for p in self.positions.values():
            floating += self._profit(p.type == ORDER_TYPE_BUY, p.volume, p.price_open, p.price_current)
        return self.balance + floating

    def margin_used(self) -> float:
        return sum(self._margin(p.volume, p.price_open) for p in self.positions.values())

    # ── Driven by the backtest engine, once per bar/tick ────────
    def advance(self, bid: float, ask: float, ts: float):
        self.bid, self.ask, self.now = bid, ask, ts

        # 1) Update floating P/L on open positions
        for p in self.positions.values():
            p.price_current = bid if p.type == ORDER_TYPE_BUY else ask
            p.profit = self._profit(p.type == ORDER_TYPE_BUY, p.volume, p.price_open, p.price_current)

        # 2) Check pending stop orders for a fill
        for ticket in list(self.pending.keys()):
            o = self.pending[ticket]
            is_buy = o.type == ORDER_TYPE_BUY_STOP
            triggered = (is_buy and ask >= o.price_open) or (not is_buy and bid <= o.price_open)
            if triggered:
                self._fill_pending(o)

        # 3) Check open positions for SL/TP hit (conservative: SL checked
        #    before TP if both would trigger on the same step, matching
        #    the live bot's own assumption that SL is the protective side)
        for ticket in list(self.positions.keys()):
            p = self.positions[ticket]
            is_buy = p.type == ORDER_TYPE_BUY
            hit_sl = p.sl > 0 and ((is_buy and bid <= p.sl) or (not is_buy and ask >= p.sl))
            hit_tp = p.tp > 0 and ((is_buy and bid >= p.tp) or (not is_buy and ask <= p.tp))
            if hit_sl:
                self._close_position(p, p.sl, DEAL_REASON_SL)
            elif hit_tp:
                self._close_position(p, p.tp, DEAL_REASON_TP)

        self.equity_curve.append((ts, self.equity(), self.balance))

    def _fill_pending(self, o):
        del self.pending[o.ticket]
        pos = SimpleNamespace(
            ticket=o.ticket, symbol=self.symbol,
            type=ORDER_TYPE_BUY if o.type == ORDER_TYPE_BUY_STOP else ORDER_TYPE_SELL,
            volume=o.volume_current, price_open=o.price_open, price_current=o.price_open,
            sl=o.sl, tp=o.tp, time=self.now, magic=o.magic, profit=0.0,
            comment=o.comment,
        )
        self.positions[pos.ticket] = pos
        self.events.append({
            "t": self.now, "kind": "FILL", "ticket": pos.ticket,
            "side": "BUY" if pos.type == ORDER_TYPE_BUY else "SELL",
            "price": pos.price_open, "lot": pos.volume,
        })

    def _close_position(self, p, price: float, reason: int):
        is_buy = p.type == ORDER_TYPE_BUY
        profit = self._profit(is_buy, p.volume, p.price_open, price)
        self.balance += profit
        del self.positions[p.ticket]
        self.deals.append(SimpleNamespace(
            position_id=p.ticket, entry=DEAL_ENTRY_OUT, price=price,
            profit=profit, time=self.now, reason=reason,
            type=p.type, volume=p.volume,
        ))
        self.events.append({
            "t": self.now, "kind": "CLOSE", "ticket": p.ticket,
            "side": "BUY" if is_buy else "SELL", "price": price,
            "profit": round(profit, 2), "balance": round(self.balance, 2),
            "reason": {DEAL_REASON_SL: "SL", DEAL_REASON_TP: "TP"}.get(reason, "?"),
        })

    def force_close_all(self, reason=DEAL_REASON_CLIENT):
        for p in list(self.positions.values()):
            price = self.bid if p.type == ORDER_TYPE_BUY else self.ask
            self._close_position(p, price, reason)
        self.pending.clear()

    # ── mt5.* function implementations ──────────────────────────
    def account_info(self):
        eq = self.equity()
        used = self.margin_used()
        return SimpleNamespace(
            balance=self.balance, equity=eq, margin=used,
            margin_free=eq - used, leverage=self.spec.leverage,
        )

    def symbol_info(self, symbol):
        s = self.spec
        return SimpleNamespace(
            digits=s.digits, point=s.point, trade_tick_size=s.trade_tick_size,
            trade_tick_value=s.trade_tick_value, trade_contract_size=s.trade_contract_size,
            volume_step=s.volume_step, volume_min=s.volume_min, volume_max=s.volume_max,
            trade_stops_level=s.trade_stops_level, filling_mode=s.filling_mode,
        )

    def symbol_info_tick(self, symbol):
        if self.bid <= 0:
            return None
        return SimpleNamespace(bid=self.bid, ask=self.ask, time=self.now)

    def symbol_select(self, symbol, enable=True):
        return True

    def positions_get(self, symbol=None):
        return [p for p in self.positions.values() if symbol is None or p.symbol == symbol]

    def orders_get(self, symbol=None):
        return [o for o in self.pending.values() if symbol is None or o.symbol == symbol]

    def history_deals_get(self, position=None):
        if position is None:
            return list(self.deals)
        return [d for d in self.deals if d.position_id == position]

    def order_calc_profit(self, order_type, symbol, lot, price_open, price_close):
        return self._profit(order_type == ORDER_TYPE_BUY, lot, price_open, price_close)

    def order_calc_margin(self, order_type, symbol, lot, price):
        return self._margin(lot, price)

    def order_send(self, request: dict):
        action = request.get("action")

        if action == TRADE_ACTION_PENDING:
            ticket = self._next_ticket()
            o = SimpleNamespace(
                ticket=ticket, symbol=self.symbol, type=request["type"],
                volume_current=request["volume"], price_open=request["price"],
                sl=request.get("sl", 0.0), tp=request.get("tp", 0.0),
                time_setup=self.now, magic=request.get("magic", 0),
                comment=request.get("comment", ""),
            )
            self.pending[ticket] = o
            return _Result(TRADE_RETCODE_DONE, order=ticket)

        if action == TRADE_ACTION_REMOVE:
            ticket = request.get("order")
            if ticket in self.pending:
                del self.pending[ticket]
                return _Result(TRADE_RETCODE_DONE, order=ticket)
            return _Result(TRADE_RETCODE_REJECT, comment="order not found")

        if action == TRADE_ACTION_SLTP:
            ticket = request.get("position")
            p = self.positions.get(ticket)
            if not p:
                return _Result(TRADE_RETCODE_REJECT, comment="position not found")
            p.sl = request.get("sl", p.sl)
            p.tp = request.get("tp", p.tp)
            return _Result(TRADE_RETCODE_DONE, order=ticket)

        if action == TRADE_ACTION_DEAL:
            # Partial/full close against an existing position
            if "position" in request:
                ticket = request["position"]
                p = self.positions.get(ticket)
                if not p:
                    return _Result(TRADE_RETCODE_REJECT, comment="position not found")
                close_vol = request["volume"]
                price = request.get("price", self.bid if p.type == ORDER_TYPE_BUY else self.ask)
                if close_vol >= p.volume - 1e-9:
                    self._close_position(p, price, DEAL_REASON_CLIENT)
                else:
                    # Partial close: realize profit on close_vol, shrink the rest
                    is_buy = p.type == ORDER_TYPE_BUY
                    profit = self._profit(is_buy, close_vol, p.price_open, price)
                    self.balance += profit
                    p.volume = round(p.volume - close_vol, 2)
                    self.deals.append(SimpleNamespace(
                        position_id=ticket, entry=DEAL_ENTRY_OUT, price=price,
                        profit=profit, time=self.now, reason=DEAL_REASON_CLIENT,
                        type=p.type, volume=close_vol,
                    ))
                    self.events.append({
                        "t": self.now, "kind": "PARTIAL_CLOSE", "ticket": ticket,
                        "closed_lot": close_vol, "remaining_lot": p.volume,
                        "profit": round(profit, 2), "balance": round(self.balance, 2),
                    })
                return _Result(TRADE_RETCODE_DONE, order=ticket)
            else:
                # New market order (auto-convert-to-market path in send_pair)
                ticket = self._next_ticket()
                is_buy = request["type"] == ORDER_TYPE_BUY
                price = request.get("price", self.ask if is_buy else self.bid)
                pos = SimpleNamespace(
                    ticket=ticket, symbol=self.symbol, type=request["type"],
                    volume=request["volume"], price_open=price, price_current=price,
                    sl=request.get("sl", 0.0), tp=request.get("tp", 0.0),
                    time=self.now, magic=request.get("magic", 0), profit=0.0,
                    comment=request.get("comment", ""),
                )
                self.positions[ticket] = pos
                self.events.append({
                    "t": self.now, "kind": "FILL_MARKET", "ticket": ticket,
                    "side": "BUY" if is_buy else "SELL", "price": price, "lot": pos.volume,
                })
                return _Result(TRADE_RETCODE_DONE, order=ticket)

        return _Result(TRADE_RETCODE_REJECT, comment=f"unhandled action {action}")

    # ── No-ops to satisfy any stray calls ───────────────────────
    def initialize(self, *a, **k):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")


_MODULE_CACHE = {}


def install(broker: BacktestBroker):
    """
    Replace sys.modules['MetaTrader5'] with this module, with every
    mt5.X(...) call routed to `broker`. Must be called BEFORE
    core.position_monitor / core.order_manager / core.resume are
    imported for the first time in this process (Python caches
    imports — if the real or a different fake module already got
    imported, re-importing won't re-bind `import MetaTrader5 as mt5`
    inside those files). The backtest CLI entry point enforces this
    ordering; see run_backtest.py.
    """
    this_module = sys.modules[__name__]
    # Bind every BacktestBroker method as a module-level function so
    # `import MetaTrader5 as mt5; mt5.account_info()` works exactly
    # like the real package's flat function namespace.
    for name in ("account_info", "symbol_info", "symbol_info_tick",
                 "symbol_select", "positions_get", "orders_get",
                 "history_deals_get", "order_calc_profit", "order_calc_margin",
                 "order_send", "initialize", "shutdown", "last_error"):
        setattr(this_module, name, getattr(broker, name))
    sys.modules["MetaTrader5"] = this_module
    _MODULE_CACHE["broker"] = broker
    return this_module