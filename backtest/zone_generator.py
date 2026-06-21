"""
backtest/zone_generator.py — Produces the list of rectangles the
backtest will trade.

Entries in this bot are deliberately 100% manual (the trader draws a
rectangle) — there is no historical record of what a trader would
have drawn on a given day. Two ways to backtest anyway:

  1. MANUAL: you supply the exact historical rectangles you would
     have drawn (a simple CSV: time, top, bottom). This is the
     trustworthy option if you kept a record of your own real
     entries, since it tests this codebase's exact mechanics
     (geometry, lot table, R1/R2/R3, kill switches) against exactly
     what you actually did.

  2. AUTO (FVG-based): rectangles are generated automatically by
     re-running the same 3-candle Fair Value Gap rule the live
     fvg_detector.py uses, against the historical OHLC bars for the
     requested range. This is a reasonable proxy for "the kind of
     zone a trader following this strategy would plausibly draw,"
     useful for stress-testing the LOT SIZING / R1-R2-R3 / KILL
     SWITCH mechanics across many zones quickly — it is NOT a claim
     that you would have drawn exactly these rectangles.

Both paths produce the same output: a time-ordered list of
`Zone(time, top, bottom)` for engine.py to feed into the bot one at
a time, in the order they would have appeared.
"""
import csv
from dataclasses import dataclass


@dataclass
class Zone:
    time: float    # unix timestamp this rectangle becomes visible
    top: float
    bottom: float
    label: str = ""


def from_csv(path: str) -> list:
    """
    CSV columns: time,top,bottom[,label]
    `time` may be a unix timestamp (int/float) or an ISO-8601 string
    (e.g. 2026-03-04T09:30:00).
    """
    import datetime as dt
    zones = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_t = row["time"].strip()
            try:
                t = float(raw_t)
            except ValueError:
                t = dt.datetime.fromisoformat(raw_t).timestamp()
            zones.append(Zone(
                time=t, top=float(row["top"]), bottom=float(row["bottom"]),
                label=row.get("label", "") or "",
            ))
    zones.sort(key=lambda z: z.time)
    return zones


def from_fvg_autodetect(bars: list, pip_size: float, min_gap_pips: float = 3.0) -> list:
    """
    bars: list of dicts/namespaces with .time/.open/.high/.low/.close
          (oldest first — the same order mt5.copy_rates_range returns).

    Faithfully reproduces core/fvg_detector.py's 3-candle gap rule
    (no mt5 dependency here — pure array scan), so the rectangles
    generated for backtesting match what the live detector would draw
    given the same bars.
    """
    min_gap_price = min_gap_pips * pip_size
    zones = []

    def g(b, k):
        return float(b[k]) if isinstance(b, dict) else float(getattr(b, k))

    for i in range(len(bars) - 2):
        left, right = bars[i], bars[i + 2]
        l_high, l_low = g(left, "high"), g(left, "low")
        r_high, r_low = g(right, "high"), g(right, "low")
        t1, t2 = g(left, "time"), g(right, "time")

        if r_low > l_high:
            gap = r_low - l_high
            if gap >= min_gap_price:
                zones.append(Zone(time=t2, top=r_low, bottom=l_high,
                                   label=f"FVG_BULL_{int(t1)}"))
        elif r_high < l_low:
            gap = l_low - r_high
            if gap >= min_gap_price:
                zones.append(Zone(time=t2, top=l_low, bottom=r_high,
                                   label=f"FVG_BEAR_{int(t1)}"))

    zones.sort(key=lambda z: z.time)
    return zones


def dedupe_overlapping(zones: list, min_separation_sec: float = 300.0) -> list:
    """
    Auto-detection on dense M1 data can produce many overlapping/
    adjacent gaps in the same micro-structure. Collapse zones that
    start within `min_separation_sec` of an already-kept zone AND
    overlap it in price, keeping the earlier (first-seen) one — this
    approximates "the trader would have already drawn a box here,
    they wouldn't draw five overlapping ones in the same minute."
    """
    kept = []
    for z in sorted(zones, key=lambda z: z.time):
        dup = False
        for k in kept:
            if abs(z.time - k.time) <= min_separation_sec:
                if z.bottom <= k.top and z.top >= k.bottom:  # price overlap
                    dup = True
                    break
        if not dup:
            kept.append(z)
    return kept