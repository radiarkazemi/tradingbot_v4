"""
rect_suggest_detector.py — Rectangle Placement Suggestion Detector
======================================================================
Suggests WHERE and HOW BIG a trader-drawn entry rectangle could go,
by finding recent consolidation/compression boxes — the same kind of
"tight range before a breakout" structure this bot's rectangle-anchor
strategy is built around (BUY-STOP above, SELL-STOP below the box).

This is visualization ONLY, exactly like fvg_detector.py / ob_detector.py
— it never places an order, never creates a SourceState, and is
structurally incapable of trading. It just draws a suggested box; the
trader decides whether to actually draw their own real rectangle
there (or anywhere else) to start a cycle.

ALGORITHM:
  A "consolidation" is a run of consecutive candles whose combined
  high-low range stays within a volatility-relative tolerance — i.e.
  price compressed into a tight box relative to how much it's been
  moving lately, rather than an arbitrary fixed pip width (so this
  behaves sensibly across symbols with very different price scales,
  same reasoning as ENTRY_GAP_TOLERANCE_FRACTION in config.py).

  Volatility reference = average true-range-ish: mean(high-low) over
  the same lookback, NOT full Wilder's ATR (no gap/previous-close
  term) — good enough for a relative size comparison, simpler to
  reason about.

  For each bar, try to grow a window forward for as long as the
  window's total high-low range stays under
  max_range_atr_mult × volatility_ref. Keep windows that reach at
  least min_bars long. Greedy + non-overlapping (advance past
  whatever window was just kept).
"""
import MetaTrader5 as mt5
import os
import time as _time
import logging
from dataclasses import dataclass
from typing import List

log = logging.getLogger("rect_suggest")

RECTSUG_PREFIX = "RECTSUG_"

# MQL5 color format is 0x00BBGGRR — amber/orange, unfilled, so it's
# visually distinct from both FVG/OB zones and the trader's own
# (always filled) tracked rectangles. Same base color across all 3
# timeframes (border WIDTH varies instead — see draw_rect_suggestions_on_chart)
# so suggestions still read as one consistent family on the chart.
COLOR_SUGGESTION = 0x0000A5FF   # orange (R=0xFF, G=0xA5, B=0x00)

RECT_EXTEND_BARS = 20

# ── Timeframe registry ──────────────────────────────────────────
# Same convention as core/mtf_fvg.py's TIMEFRAME_SPECS, reused here
# for consistency rather than inventing a second pattern for the same
# idea. Border width (in draw_rect_suggestions_on_chart) scales with
# this so a 15M box reads as more significant than a 1M one at a glance.
TIMEFRAME_SPECS = {
    "15M": {"mt5_tf": None, "default_lookback": 100, "border_width": 3},
    "5M":  {"mt5_tf": None, "default_lookback": 150, "border_width": 2},
    "1M":  {"mt5_tf": None, "default_lookback": 200, "border_width": 1},
}


def _init_timeframe_specs():
    """mt5.TIMEFRAME_* constants aren't available until the real (or
    fake, in backtests) MetaTrader5 module is actually imported — fill
    them in lazily on first use rather than at module import time."""
    if TIMEFRAME_SPECS["15M"]["mt5_tf"] is None:
        TIMEFRAME_SPECS["15M"]["mt5_tf"] = mt5.TIMEFRAME_M15
        TIMEFRAME_SPECS["5M"]["mt5_tf"] = mt5.TIMEFRAME_M5
        TIMEFRAME_SPECS["1M"]["mt5_tf"] = mt5.TIMEFRAME_M1


TIMEFRAME_ORDER = ["15M", "5M", "1M"]   # largest -> smallest


@dataclass
class RectSuggestion:
    top:      float
    bottom:   float
    time1:    int     # unix time of the window's first candle
    time2:    int     # unix time of the window's last candle
    bars:     int     # how many candles wide
    timeframe: str    # "15M" / "5M" / "1M" — which source timeframe found this box
    height_pips: float
    name:     str


def get_pip_size(symbol: str) -> float:
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None:
        sym = symbol.upper()
        if "JPY" in sym:
            return 0.01
        if "XAU" in sym:
            return 0.10
        if "XAG" in sym:
            return 0.01
        if any(x in sym for x in ["US30", "NAS", "DAX", "FTSE", "SPX"]):
            return 1.0
        return 0.0001
    if info.digits in (0, 1):
        return 1.0
    if info.digits in (2, 3):
        return info.point * 10
    return info.point * 10


def _scan_one_timeframe(symbol: str, tf_key: str, mt5_tf, lookback: int,
                        min_bars: int, max_range_atr_mult: float,
                        max_suggestions: int) -> List[RectSuggestion]:
    """
    Scan the last `lookback` candles of ONE timeframe for consolidation
    boxes. Returns a list sorted newest first, capped at
    `max_suggestions`. Internal — see detect_rect_suggestions_mtf for
    the public multi-timeframe entry point.
    """
    pip = get_pip_size(symbol)
    bars = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, lookback)
    if bars is None or len(bars) < min_bars + 1:
        log.warning(
            "Not enough %s candle data for rectangle suggestions on %s", tf_key, symbol)
        return []

    # Volatility reference: mean bar range over the lookback window —
    # a simple, scale-aware stand-in for ATR (no previous-close/gap
    # term, just high-low), good enough for a relative comparison.
    ranges = [float(b["high"]) - float(b["low"]) for b in bars]
    vol_ref = sum(ranges) / len(ranges) if ranges else 0.0
    if vol_ref <= 0:
        return []

    max_range = vol_ref * max_range_atr_mult

    suggestions = []
    i = 0
    n = len(bars)
    while i < n - min_bars:
        hi = float(bars[i]["high"])
        lo = float(bars[i]["low"])
        j = i + 1
        # Grow the window forward while it stays inside tolerance
        while j < n:
            new_hi = max(hi, float(bars[j]["high"]))
            new_lo = min(lo, float(bars[j]["low"]))
            if (new_hi - new_lo) <= max_range:
                hi, lo = new_hi, new_lo
                j += 1
            else:
                break

        width = j - i
        if width >= min_bars:
            t1 = int(bars[i]["time"])
            t2 = int(bars[j - 1]["time"])
            suggestions.append(RectSuggestion(
                top=hi, bottom=lo, time1=t1, time2=t2, bars=width,
                timeframe=tf_key,
                height_pips=round((hi - lo) / pip, 1),
                name=f"{RECTSUG_PREFIX}{tf_key}_{t1}",
            ))
            i = j  # non-overlapping: skip past this whole window
        else:
            i += 1

    suggestions.sort(key=lambda s: s.time1, reverse=True)
    suggestions = suggestions[:max_suggestions]
    log.info("Rectangle suggestions (%s): %d candles -> %d boxes (max_range=%.1fpips)",
             tf_key, len(bars), len(suggestions), max_range / pip)
    return suggestions


def detect_rect_suggestions_mtf(symbol: str,
                                timeframes: List[str] = None,
                                min_bars: int = 6,
                                max_range_atr_mult: float = 1.5,
                                max_suggestions: int = 10,
                                lookback_map: dict = None) -> List[RectSuggestion]:
    """
    Scan 15M, 5M, and 1M SIMULTANEOUSLY — all three are independent
    sources, not a confluence/overlap requirement like
    core/mtf_fvg.py's cascade (a box only needs to be a genuine
    consolidation on ITS OWN timeframe to be suggested; it doesn't
    need to also line up with a box on another timeframe). Each
    suggestion is tagged with which timeframe found it
    (RectSuggestion.timeframe), and drawn with a border width that
    scales with that timeframe (see draw_rect_suggestions_on_chart) so
    a 15M box reads as more significant than a 1M one at a glance.

    `min_bars`/`max_range_atr_mult` apply uniformly across all three
    timeframes — deliberately not exposed as 3 separate settings, to
    keep the GUI simple; each timeframe's own bar size already scales
    the effective sensitivity appropriately (a 6-bar window means
    something very different on 15M vs 1M, which is exactly the point
    of scanning all three).
    """
    _init_timeframe_specs()
    if timeframes is None:
        timeframes = list(TIMEFRAME_ORDER)
    lookback_map = lookback_map or {}

    all_suggestions = []
    for tf_key in timeframes:
        if tf_key not in TIMEFRAME_SPECS:
            continue
        spec = TIMEFRAME_SPECS[tf_key]
        lookback = lookback_map.get(tf_key, spec["default_lookback"])
        all_suggestions += _scan_one_timeframe(
            symbol, tf_key, spec["mt5_tf"], lookback,
            min_bars, max_range_atr_mult, max_suggestions,
        )

    all_suggestions.sort(key=lambda s: s.time1, reverse=True)
    return all_suggestions[:max_suggestions]


def detect_rect_suggestions(symbol: str, timeframe=None, lookback: int = 200,
                            min_bars: int = 6, max_range_atr_mult: float = 1.5,
                            max_suggestions: int = 10) -> List[RectSuggestion]:
    """Back-compat single-timeframe wrapper (defaults to 1M) — prefer
    detect_rect_suggestions_mtf for the real 15M+5M+1M behavior."""
    _init_timeframe_specs()
    if timeframe is None:
        timeframe = mt5.TIMEFRAME_M1
    tf_key = next((k for k, v in TIMEFRAME_SPECS.items()
                  if v["mt5_tf"] == timeframe), "1M")
    return _scan_one_timeframe(symbol, tf_key, timeframe, lookback,
                               min_bars, max_range_atr_mult, max_suggestions)


# ── Chart drawing ─────────────────────────────────────────────────────

def _command_file(symbol: str) -> str:
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(
        appdata, "MetaQuotes", "Terminal", "Common", "Files",
        f"trader_commands_{symbol}.txt"
    )


def _write_commands(symbol: str, commands: list):
    path = _command_file(symbol)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for attempt in range(5):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(commands) + "\n")
            return
        except PermissionError:
            _time.sleep(0.05)


_PERIOD_SECONDS = {"15M": 900, "5M": 300, "1M": 60}


def draw_rect_suggestions_on_chart(symbol: str, suggestions: List[RectSuggestion],
                                   max_draw: int = 10):
    """
    Clear old suggestion boxes and draw new ones — UNFILLED (fill=0),
    consistent orange color, so they read as "consider drawing here"
    rather than a real tracked rectangle (which is always filled).

    Border width scales with each suggestion's OWN source timeframe
    (15M thickest, 1M thinnest — see TIMEFRAME_SPECS) so a glance at
    the chart tells you which timeframe a box came from without
    needing a text label.
    """
    commands = [f"DELETE_PREFIX|{RECTSUG_PREFIX}"]

    drawn = 0
    for s in suggestions:
        if drawn >= max_draw:
            break
        bar_sec = _PERIOD_SECONDS.get(s.timeframe, 60)
        t2_ext = s.time2 + bar_sec * RECT_EXTEND_BARS
        width = TIMEFRAME_SPECS.get(s.timeframe, {}).get("border_width", 1)
        # Format: DRAW_RECT|name|t1|top|t2|bottom|color|border_width|fill(0/1)
        commands.append(
            f"DRAW_RECT|{s.name}|{s.time1}|{s.top}|"
            f"{t2_ext}|{s.bottom}|{COLOR_SUGGESTION}|{width}|0"
        )
        drawn += 1

    _write_commands(symbol, commands)
    log.info("Rectangle suggestions: drew %d boxes", drawn)


def clear_rect_suggestions_on_chart(symbol: str):
    _write_commands(symbol, [f"DELETE_PREFIX|{RECTSUG_PREFIX}"])
