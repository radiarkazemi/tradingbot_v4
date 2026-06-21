"""
backtest/data_loader.py — Pull REAL historical data from MT5.

This module MUST run on the machine with a live MT5 terminal installed
and the real `MetaTrader5` Python package (Windows). It is the only
file in backtest/ that touches the real mt5 package — everything else
(engine.py, fake_mt5.py) works on whatever data series this produces,
with no idea whether it came from a live terminal or a saved file.

Two data sources, in order of preference:
  1. Tick data (mt5.copy_ticks_range) — real bid/ask, exact spread,
     matches the live bot's own tick-based touch detection precisely.
     This is what you want for trustworthy results.
  2. Bar data (mt5.copy_rates_range) — only OHLC (one price line, no
     separate bid/ask), spread is then a configured constant. Lighter
     and always available, but touch/fill timing within a bar is
     approximated (worst-reasonable-case ordering: see engine.py).

Most brokers only keep a few months to ~1 year of tick history in the
terminal's local cache — if copy_ticks_range comes back empty/short
for an older range, fall back to bars automatically.
"""
import datetime as dt


def _require_real_mt5():
    """Import the REAL MetaTrader5 package — must not already be
    monkeypatched to backtest.fake_mt5 in this process. Load data
    BEFORE calling fake_mt5.install(), or load it in a separate
    process and pass --data-file instead (see run_backtest.py)."""
    import MetaTrader5 as mt5
    if getattr(mt5, "__file__", "") and "fake_mt5" in mt5.__file__:
        raise RuntimeError(
            "MetaTrader5 is already patched to the fake backtest broker "
            "in this process. Load data BEFORE calling fake_mt5.install(), "
            "or load it in a separate process and pass --data-file instead."
        )
    return mt5


def connect(login=None, password=None, server=None):
    mt5 = _require_real_mt5()
    if login and password and server:
        ok = mt5.initialize(login=login, password=password, server=server)
    else:
        ok = mt5.initialize()
    if not ok:
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
    return mt5


def get_symbol_spec(symbol: str):
    """Pull REAL contract specs for the symbol — never hand-guess
    these for anything touching margin/profit math."""
    mt5 = _require_real_mt5()
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol_info({symbol}) returned None — is it in Market Watch?")
    acct = mt5.account_info()
    leverage = acct.leverage if acct else 100
    from backtest.fake_mt5 import SymbolSpec
    return SymbolSpec(
        symbol=symbol,
        digits=info.digits,
        point=info.point,
        trade_tick_size=info.trade_tick_size,
        trade_tick_value=info.trade_tick_value,
        trade_contract_size=info.trade_contract_size,
        volume_step=info.volume_step,
        volume_min=info.volume_min,
        volume_max=info.volume_max,
        trade_stops_level=info.trade_stops_level,
        filling_mode=info.filling_mode,
        leverage=leverage,
    )


def _weekend_warning(date_from: dt.datetime, date_to: dt.datetime) -> str:
    """
    Most symbols (forex, metals, indices CFDs) don't trade Friday
    ~21:00 UTC through Sunday ~21:00 UTC (exact close/open varies by
    broker). A request entirely inside that window will always come
    back empty — this isn't a connection problem, there's just no
    data to return. Cheap, broker-agnostic heuristic: flag it if
    every day in the range is a Saturday or Sunday.
    """
    days = (date_to - date_from).days + 1
    if days <= 3:
        cur = date_from
        all_weekend = True
        for _ in range(days):
            if cur.weekday() < 5:  # Mon=0 .. Sun=6; 5=Sat, 6=Sun
                all_weekend = False
                break
            cur += dt.timedelta(days=1)
        if all_weekend:
            return (
                f"\n  NOTE: {date_from.date()} to {date_to.date()} falls entirely on a "
                f"weekend — most symbols don't trade Sat/Sun, so an empty result here is "
                f"expected, not a connection error. Try a weekday range instead "
                f"(e.g. the preceding Friday)."
            )
    return ""


def load_ticks(symbol: str, date_from: dt.datetime, date_to: dt.datetime):
    """
    Returns a list of (timestamp_float, bid, ask) tuples, or None if
    no tick history is available for this range (caller should fall
    back to load_bars).
    """
    mt5 = _require_real_mt5()
    ticks = mt5.copy_ticks_range(symbol, date_from, date_to, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return None
    out = []
    for t in ticks:
        bid, ask = float(t["bid"]), float(t["ask"])
        if bid <= 0 or ask <= 0:
            continue
        out.append((float(t["time"]), bid, ask))
    return out if out else None


def load_bars(symbol: str, date_from: dt.datetime, date_to: dt.datetime,
              timeframe=None, spread_points: float = None):
    """
    Returns a list of (timestamp_float, bid, ask) tuples synthesized
    from OHLC bars: each bar contributes its open/high/low/close as
    four sequential price points (a coarse but real-data-grounded
    approximation of intra-bar movement), with ask = bid + configured
    spread. Order within the bar is O -> the worse-of-{H,L} first ->
    the other -> C, which is the conservative assumption for whichever
    direction the touch/SL/TP logic cares about (see engine.py).
    """
    mt5 = _require_real_mt5()
    tf = timeframe if timeframe is not None else mt5.TIMEFRAME_M1
    rates = mt5.copy_rates_range(symbol, tf, date_from, date_to)
    if rates is None or len(rates) == 0:
        code, desc = mt5.last_error()
        hint = _weekend_warning(date_from, date_to)
        sel = mt5.symbol_info(symbol)
        sel_hint = ""
        if sel is None:
            sel_hint = (f"\n  NOTE: symbol_info('{symbol}') is None — check this exact "
                       f"spelling is in your broker's Market Watch (right-click Market "
                       f"Watch -> Symbols to search/add it). Many brokers suffix names, "
                       f"e.g. 'XAUUSD.a', 'XAUUSDm', 'GOLD#'.")
        raise RuntimeError(
            f"copy_rates_range returned no data for {symbol} {date_from}..{date_to} "
            f"(mt5.last_error()={code}:{desc!r}){hint}{sel_hint}\n"
            f"  Other common causes: the terminal hasn't downloaded that much history "
            f"for this symbol/timeframe yet — open an {symbol} M1 chart in the terminal "
            f"itself and scroll/press Home to force it to backfill, then retry."
        )
    info = mt5.symbol_info(symbol)
    spread = (spread_points * info.point) if spread_points else (
        info.spread * info.point if info.spread else info.point * 10)

    out = []
    for r in rates:
        o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        t0 = float(r["time"])
        step = 60.0 / 4  # 4 sub-points per M1 bar
        if abs(h - o) >= abs(o - l):
            path = [o, h, l, c]
        else:
            path = [o, l, h, c]
        for i, price in enumerate(path):
            out.append((t0 + i * step, price, price + spread))
    return out


def load_history(symbol: str, date_from: dt.datetime, date_to: dt.datetime,
                  prefer_ticks: bool = True, spread_points: float = None):
    """High-level entry point: try ticks, fall back to M1 bars."""
    if prefer_ticks:
        ticks = load_ticks(symbol, date_from, date_to)
        if ticks:
            return ticks, "ticks"
    bars = load_bars(symbol, date_from, date_to, spread_points=spread_points)
    return bars, "bars"


def export_to_file(symbol: str, date_from: dt.datetime, date_to: dt.datetime,
                    out_path: str, prefer_ticks: bool = True, spread_points: float = None):
    """
    Run this step ON THE WINDOWS MACHINE with the real MT5 terminal.
    Saves (symbol spec + price series) to a single JSON file that
    run_backtest.py can then consume on ANY machine, with no MT5
    package/terminal required at backtest-run time. Recommended if
    you want to iterate on backtest settings without re-pulling data
    from MT5 every run.
    """
    import json
    connect()
    spec = get_symbol_spec(symbol)
    series, source = load_history(symbol, date_from, date_to, prefer_ticks, spread_points)
    payload = {
        "symbol": symbol,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "source": source,
        "spec": vars(spec),
        "series": series,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f)
    print(f"Exported {len(series)} {source} points for {symbol} -> {out_path}")


def load_from_file(path: str):
    """Load a payload previously written by export_to_file()."""
    import json
    from backtest.fake_mt5 import SymbolSpec
    with open(path) as f:
        payload = json.load(f)
    spec = SymbolSpec(**payload["spec"])
    series = [tuple(p) for p in payload["series"]]
    return spec, series, payload["source"]