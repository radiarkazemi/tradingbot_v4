"""
core/bias_watcher.py — ICT Multi-Timeframe Bias background scanner
====================================================================
Follows the exact same daemon-thread pattern as FVGWatcher/OBWatcher.
Scans M1→H1 every `scan_interval` seconds and emits a formatted
summary log line every time the bias picture changes on any timeframe.

Designed to be completely independent — does NOT require the EA file,
does NOT draw on chart, purely analytical output to the log panel.
"""
import threading
import time as _time
import logging
from typing import Callable, Dict, Optional

import MetaTrader5 as mt5
from core.bias_detector import (
    analyze_all_timeframes, TimeframeBias,
    BIAS_TIMEFRAMES, TF_NAMES,
)

log = logging.getLogger("bias_watcher")


class BiasWatcher(threading.Thread):
    """
    Standalone daemon thread.
    Calls analyze_all_timeframes() every scan_interval seconds.
    Emits a one-line summary to the log panel on every change.
    Also stores latest results for GUI to read at any time.
    """

    def __init__(self,
                 symbol: str,
                 lookback: int = 100,
                 scan_interval: float = 10.0,
                 log_fn: Optional[Callable] = None,
                 on_results: Optional[Callable] = None):
        super().__init__(daemon=True)
        self.symbol = symbol
        self.lookback = lookback
        self.scan_interval = scan_interval
        self._log = log_fn or (lambda msg, level="INFO": log.info(msg))
        self._on_results = on_results
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: Dict[str, TimeframeBias] = {}
        self._last_signature: str = ""

    def stop(self):
        self._stop_event.set()

    def get_latest(self) -> Dict[str, TimeframeBias]:
        """Thread-safe read of latest bias results."""
        with self._lock:
            return dict(self._latest)

    def _build_signature(self, results: dict) -> str:
        """
        Compact fingerprint of the current bias picture.
        Used to detect when anything has changed since last scan.
        """
        return "|".join(
            f"{name}:{b.direction[:1]}{b.bull_pct:.0f}"
            for name, b in sorted(results.items())
        )

    def _build_summary_line(self, results: dict) -> str:
        """
        Build the single log line shown in the GUI on every change.
        Example:
          🧭 Bias | M1:🟢68% M5:🔴61% M15:⚪52% M30:🟢71% H1:🟢75%
          → Net: 3🟢 1🔴 1⚪ | Dominant: BULLISH (Strong)
        """
        parts = []
        bull_count = bear_count = neutral_count = 0

        for name in ["M1", "M5", "M15", "M30", "H1"]:
            b = results.get(name)
            if b is None:
                parts.append(f"{name}:❓")
                continue
            if b.direction == "BULL":
                bull_count += 1
                pct = f"{b.bull_pct:.0f}%"
            elif b.direction == "BEAR":
                bear_count += 1
                pct = f"{b.bear_pct:.0f}%"
            else:
                neutral_count += 1
                pct = "~50%"
            parts.append(f"{name}:{b.emoji}{pct}")

        summary = "  ".join(parts)

        # Overall dominant bias
        if bull_count > bear_count and bull_count > neutral_count:
            dominant = "BULLISH"
        elif bear_count > bull_count and bear_count > neutral_count:
            dominant = "BEARISH"
        else:
            dominant = "MIXED / NEUTRAL"

        # Confidence from average spread
        all_biases = [b for b in results.values() if b is not None]
        if all_biases:
            avg_spread = sum(abs(b.bull_pct - b.bear_pct)
                             for b in all_biases) / len(all_biases)
            conf = "Strong" if avg_spread >= 35 else (
                "Moderate" if avg_spread >= 18 else "Weak")
        else:
            conf = "—"

        return (
            f"🧭  Bias | {summary}\n"
            f"   → {bull_count}🟢 {bear_count}🔴 {neutral_count}⚪  "
            f"| Dominant: {dominant} ({conf})"
        )

    def run(self):
        self._log(
            f"🧭  Bias Watcher started | {self.symbol} M1→H1 | "
            f"ICT: Structure+P/D+FVG+OB+PDH/PDL+Momentum | "
            f"scan={self.scan_interval:.0f}s", "INFO"
        )

        # Brief startup delay so MT5 initializes
        self._stop_event.wait(3.0)

        while not self._stop_event.is_set():
            try:
                results = analyze_all_timeframes(
                    symbol=self.symbol,
                    lookback=self.lookback,
                )

                with self._lock:
                    self._latest = results

                sig = self._build_signature(results)
                if sig != self._last_signature:
                    self._last_signature = sig
                    if results:
                        line = self._build_summary_line(results)
                        self._log(line, "NEW")
                        if self._on_results:
                            try:
                                self._on_results(results)
                            except Exception:
                                pass

            except Exception as e:
                self._log(
                    f"💥  Bias scan error: {type(e).__name__}: {e}", "ERROR")

            self._stop_event.wait(self.scan_interval)

        self._log("🧭  Bias Watcher stopped", "INFO")
