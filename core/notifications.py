"""
core/notifications.py — Desktop notification engine for TraderBot v4

Detects trade events by scanning log messages (TP, SL, order placed,
risk-free/loss-free locked, balance TP, margin warning) and fires:
  • Windows toast notification (via QSystemTrayIcon)
  • Optional sound (via winsound on Windows, beep fallback elsewhere)

Each event type has its own enable flag, sound flag, and can be
toggled from the Notifications settings panel in the GUI.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable

log = logging.getLogger("notifications")

# ── Event types ───────────────────────────────────────────────────
EV_TP = "tp"           # Position hit take-profit (win)
EV_SL = "sl"           # Position hit stop-loss (loss)
EV_RISK_FREE = "risk_free"    # Risk-free / loss-free SL locked
EV_ORDER_PLACE = "order_placed"  # New order pair placed
EV_BALANCE_TP = "balance_tp"   # Account balance target reached
EV_MARGIN = "margin_warn"  # Margin protection triggered
EV_BOT_START = "bot_start"    # Bot started
EV_BOT_STOP = "bot_stop"     # Bot stopped

EVENT_LABELS = {
    EV_TP:          "🏆  Take-Profit hit",
    EV_SL:          "📉  Stop-Loss hit",
    EV_RISK_FREE:   "🛡️   Risk/Loss-Free locked",
    EV_ORDER_PLACE: "📌  Order pair placed",
    EV_BALANCE_TP:  "🎯  Balance TP reached",
    EV_MARGIN:      "🛡️   Margin protection",
    EV_BOT_START:   "▶   Bot started",
    EV_BOT_STOP:    "■   Bot stopped",
}

# Regex patterns that match log messages → event type
_PATTERNS = [
    (EV_TP,          re.compile(r"🏆|hit TP|TP — round won", re.I)),
    (EV_SL,          re.compile(r"📉.*closed.*\(sl\)|closed.*\(sl\)", re.I)),
    (EV_RISK_FREE,   re.compile(r"🟩.*loss.free|🛡.*risk.free", re.I)),
    (EV_ORDER_PLACE, re.compile(r"📌.*pair placed|R\d+ pair placed", re.I)),
    (EV_BALANCE_TP,  re.compile(r"balance tp|🎯.*balance", re.I)),
    (EV_MARGIN,      re.compile(r"margin protection|margin.limited", re.I)),
    (EV_BOT_START,   re.compile(r"connected.*balance|✅.*connected", re.I)),
    (EV_BOT_STOP,    re.compile(r"bot stopped|■.*stop", re.I)),
]

# ── Per-event settings ────────────────────────────────────────────


@dataclass
class EventConfig:
    enabled: bool = True
    sound:   bool = True


@dataclass
class NotificationSettings:
    events: Dict[str, EventConfig] = field(default_factory=lambda: {
        ev: EventConfig() for ev in EVENT_LABELS
    })
    # Global switches
    notifications_enabled: bool = True
    sound_enabled:         bool = True
    # Per-event sounds (Windows .wav or empty for system beep)
    # Users can customise by setting e.g. sounds["tp"] = "C:/sounds/win.wav"
    sounds: Dict[str, str] = field(default_factory=lambda: {
        EV_TP:          "",   # empty = system beep
        EV_SL:          "",
        EV_RISK_FREE:   "",
        EV_ORDER_PLACE: "",
        EV_BALANCE_TP:  "",
        EV_MARGIN:      "",
        EV_BOT_START:   "",
        EV_BOT_STOP:    "",
    })


class NotificationManager:
    """
    Singleton-style manager. Attach to _on_log by calling
    manager.check(msg, level) on every log line.
    Set tray_icon to a QSystemTrayIcon instance for desktop popups.
    """

    def __init__(self, settings: Optional[NotificationSettings] = None):
        self.settings = settings or NotificationSettings()
        self.tray_icon = None          # set to QSystemTrayIcon after GUI builds
        self._last_ev: Dict[str, float] = {}   # throttle: event → last_time
        self._throttle_sec = 3.0        # min seconds between same event type

    def check(self, msg: str, level: str = "INFO"):
        """
        Call this on every log line. Detects events and fires
        notifications + sounds when appropriate.
        """
        if not self.settings.notifications_enabled:
            return
        import time
        now = time.monotonic()
        for ev_type, pattern in _PATTERNS:
            if not pattern.search(msg):
                continue
            cfg = self.settings.events.get(ev_type)
            if not cfg or not cfg.enabled:
                continue
            # Throttle same event type
            if now - self._last_ev.get(ev_type, 0) < self._throttle_sec:
                continue
            self._last_ev[ev_type] = now
            self._fire(ev_type, msg, cfg)
            break   # one event per log line

    def _fire(self, ev_type: str, msg: str, cfg: EventConfig):
        label = EVENT_LABELS.get(ev_type, ev_type)
        # Extract first meaningful line for body
        body = msg.strip().split("\n")[0][:120]

        # Desktop toast
        if self.tray_icon is not None:
            try:
                from PyQt5.QtWidgets import QSystemTrayIcon
                icon_map = {
                    EV_TP:          QSystemTrayIcon.Information,
                    EV_SL:          QSystemTrayIcon.Warning,
                    EV_RISK_FREE:   QSystemTrayIcon.Information,
                    EV_ORDER_PLACE: QSystemTrayIcon.Information,
                    EV_BALANCE_TP:  QSystemTrayIcon.Information,
                    EV_MARGIN:      QSystemTrayIcon.Critical,
                    EV_BOT_START:   QSystemTrayIcon.Information,
                    EV_BOT_STOP:    QSystemTrayIcon.Warning,
                }
                icon = icon_map.get(ev_type, QSystemTrayIcon.Information)
                self.tray_icon.showMessage(
                    f"TraderBot v4 — {label}", body, icon, 4000)
            except Exception as e:
                log.debug("Tray notification failed: %s", e)

        # Sound
        if self.settings.sound_enabled and cfg.sound:
            self._play_sound(ev_type)

    def _play_sound(self, ev_type: str):
        """Play sound for the event. Windows: winsound. Other: no-op."""
        import sys
        wav = self.settings.sounds.get(ev_type, "")
        try:
            if sys.platform == "win32":
                import winsound
                if wav:
                    winsound.PlaySound(
                        wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
                else:
                    # Different beep frequencies per event type
                    freq_map = {
                        EV_TP:          1200,
                        EV_SL:          400,
                        EV_RISK_FREE:   900,
                        EV_ORDER_PLACE: 700,
                        EV_BALANCE_TP:  1400,
                        EV_MARGIN:      300,
                        EV_BOT_START:   800,
                        EV_BOT_STOP:    500,
                    }
                    dur_map = {
                        EV_TP:          300,
                        EV_SL:          500,
                        EV_BALANCE_TP:  600,
                        EV_MARGIN:      800,
                    }
                    freq = freq_map.get(ev_type, 700)
                    dur = dur_map.get(ev_type, 200)
                    winsound.Beep(freq, dur)
        except Exception as e:
            log.debug("Sound play failed: %s", e)


# Module-level singleton — GUI imports this
manager = NotificationManager()
