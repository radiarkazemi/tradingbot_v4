"""
rect_suggest_watcher.py — Rectangle Suggestion background scanner
======================================================================
Completely independent thread — does NOT wait for the EA file or
chart lines, and NEVER trades. Scans MT5 candles directly via the
Python API for consolidation/compression boxes and draws them as
suggestions (see rect_suggest_detector.py for the detection logic and
why this is visualization-only, structurally incapable of placing an
order).
"""
import threading
import time as _time
import logging
import MetaTrader5 as mt5
from core.rect_suggest_detector import (
    detect_rect_suggestions, draw_rect_suggestions_on_chart,
    clear_rect_suggestions_on_chart,
)

log = logging.getLogger("rect_suggest_watcher")

TIMEFRAME_NAMES = {
    mt5.TIMEFRAME_M1:  "M1",
    mt5.TIMEFRAME_M5:  "M5",
    mt5.TIMEFRAME_M15: "M15",
    mt5.TIMEFRAME_M30: "M30",
    mt5.TIMEFRAME_H1:  "H1",
    mt5.TIMEFRAME_H4:  "H4",
    mt5.TIMEFRAME_D1:  "D1",
}


class RectSuggestWatcher(threading.Thread):
    """
    Standalone daemon thread that:
    1. Scans MT5 candles for consolidation boxes every scan_interval seconds
    2. Logs found suggestions (always works, no EA needed)
    3. Tries to draw them on chart via the command file (optional,
       skips silently if the EA isn't running)
    4. Exposes latest suggestions via self.latest_suggestions for other
       modules to read

    Enable/disable is just starting/stopping this thread (see gui.py) —
    there's no separate internal toggle since it has no side effects
    to undo beyond clearing its own drawn boxes on stop.
    """

    def __init__(self, symbol: str, timeframe=None,
                 min_bars: int = 6,
                 max_range_atr_mult: float = 1.5,
                 lookback: int = 200,
                 scan_interval: float = 5.0,
                 max_draw: int = 10,
                 draw_on_chart: bool = True,
                 log_fn=None):
        super().__init__(daemon=True)
        self.symbol             = symbol
        self.timeframe          = timeframe or mt5.TIMEFRAME_M1
        self.min_bars           = min_bars
        self.max_range_atr_mult = max_range_atr_mult
        self.lookback           = lookback
        self.scan_interval      = scan_interval
        self.max_draw           = max_draw
        self.draw_on_chart      = draw_on_chart
        self._log               = log_fn or (lambda msg, level="INFO": log.info(msg))
        self._stop              = threading.Event()

        self.latest_suggestions = []
        self._last_count        = -1
        self._lock              = threading.Lock()

    def stop(self):
        self._stop.set()

    def get_suggestions(self):
        """Thread-safe access to the latest detected suggestions."""
        with self._lock:
            return list(self.latest_suggestions)

    def update_settings(self, min_bars: int = None, max_range_atr_mult: float = None,
                        lookback: int = None, max_draw: int = None,
                        draw_on_chart: bool = None):
        """Hot-update settings without restarting."""
        if min_bars           is not None: self.min_bars           = min_bars
        if max_range_atr_mult is not None: self.max_range_atr_mult = max_range_atr_mult
        if lookback           is not None: self.lookback           = lookback
        if max_draw           is not None: self.max_draw           = max_draw
        if draw_on_chart      is not None: self.draw_on_chart      = draw_on_chart
        self._last_count = -1  # force re-log on next scan

    def run(self):
        tf_name = TIMEFRAME_NAMES.get(self.timeframe, str(self.timeframe))
        self._log(
            f"🟧  Rectangle Suggestions started | {self.symbol} {tf_name} | "
            f"min_bars={self.min_bars} max_range={self.max_range_atr_mult}×avg-range "
            f"(suggestion only — never places an order)", "INFO"
        )

        self._stop.wait(2.0)

        while not self._stop.is_set():
            try:
                suggestions = detect_rect_suggestions(
                    symbol             = self.symbol,
                    timeframe          = self.timeframe,
                    lookback           = self.lookback,
                    min_bars           = self.min_bars,
                    max_range_atr_mult = self.max_range_atr_mult,
                    max_suggestions    = self.max_draw,
                )

                with self._lock:
                    self.latest_suggestions = suggestions

                if len(suggestions) != self._last_count:
                    self._last_count = len(suggestions)
                    if suggestions:
                        top3 = suggestions[:3]
                        summary = "  ".join(
                            f"🟧{s.height_pips:.1f}p/{s.bars}bars" for s in top3
                        )
                        self._log(
                            f"🟧  Suggestions: {len(suggestions)} box(es) | "
                            f"top3: {summary}", "NEW"
                        )
                    else:
                        self._log(
                            f"🟧  Suggestions: 0 boxes found (try lowering "
                            f"max_range or min_bars)", "INFO"
                        )

                if self.draw_on_chart and suggestions:
                    try:
                        draw_rect_suggestions_on_chart(
                            self.symbol, suggestions,
                            max_draw=self.max_draw, timeframe=self.timeframe)
                    except Exception:
                        pass  # EA not running — detection still works

            except Exception as e:
                self._log(f"💥 Rectangle suggestion scan error: {type(e).__name__}: {e}", "ERROR")

            self._stop.wait(self.scan_interval)

        if self.draw_on_chart:
            try:
                clear_rect_suggestions_on_chart(self.symbol)
            except Exception:
                pass

        self._log("🟧  Rectangle Suggestions stopped", "INFO")