"""gui/widgets.py — Shared Qt widget primitives (Sig, Sparkline, stat card)."""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QSizePolicy
from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QLinearGradient, QPen
from .theme import C, SS


class Sig(QObject):
    log_line = pyqtSignal(str, str)
    status = pyqtSignal(str)
    state = pyqtSignal(list)
    candle = pyqtSignal(dict)
    balance_tp = pyqtSignal()   # ← NEW: fired when balance TP hit → GUI stops cleanly
    # emitted by bias watcher with latest results
    bias_update = pyqtSignal(dict)

# ── Helpers ───────────────────────────────────────────────────────


def _vline():
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setStyleSheet(f"color:{C['border']};")
    return f


def _hline():
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color:{C['border']};")
    return f


class Sparkline(QWidget):
    """
    Tiny embedded real-time line chart — no extra dependencies, just
    QPainter. Fed live equity samples from the existing refresh timer;
    draws a smooth filled trend line that's green when the visible
    window is trending up, red when trending down. Gives an
    at-a-glance read on account trajectory without needing a separate
    chart window.
    """

    def __init__(self, max_points: int = 80, parent=None):
        super().__init__(parent)
        self._values = []
        self._max_points = max_points
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def add_value(self, v: float):
        self._values.append(v)
        if len(self._values) > self._max_points:
            self._values.pop(0)
        self.update()

    def clear(self):
        self._values = []
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if len(self._values) < 2:
            painter.setPen(QColor(C['txt3']))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "— waiting for live data —")
            return
        vmin, vmax = min(self._values), max(self._values)
        if vmax - vmin < 1e-9:
            vmax = vmin + 1.0
        rising = self._values[-1] >= self._values[0]
        color = QColor(C['green']) if rising else QColor(C['red'])
        n = len(self._values)
        pad = 4

        def pt(i, v):
            x = pad + (w - 2 * pad) * (i / (n - 1))
            y = h - pad - (h - 2 * pad) * ((v - vmin) / (vmax - vmin))
            return x, y

        path = QPainterPath()
        x0, y0 = pt(0, self._values[0])
        path.moveTo(x0, y0)
        for i, v in enumerate(self._values[1:], start=1):
            x, y = pt(i, v)
            path.lineTo(x, y)

        fill_path = QPainterPath(path)
        xl, _ = pt(n - 1, self._values[-1])
        fill_path.lineTo(xl, h - pad)
        fill_path.lineTo(x0, h - pad)
        fill_path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        top = QColor(color)
        top.setAlpha(90)
        bottom = QColor(color)
        bottom.setAlpha(0)
        grad.setColorAt(0, top)
        grad.setColorAt(1, bottom)
        painter.fillPath(fill_path, grad)

        painter.setPen(QPen(color, 1.6))
        painter.drawPath(path)


def _stat_card(title: str, accent_color: str, big: bool = False):
    """
    Builds a small KPI "card" — colored top accent bar, a faint
    uppercase title, and a large bold value label. Returns
    (card_frame, value_label) — the caller keeps the value_label
    reference to update text on it; everything else is just chrome.
    """
    card = QFrame()
    card.setStyleSheet(
        f"QFrame {{ background:{C['input']}; border:1px solid {C['border']}; "
        f"border-top:2px solid {accent_color}; border-radius:5px; }}"
    )
    cl = QVBoxLayout(card)
    cl.setContentsMargins(10, 6, 10, 8)
    cl.setSpacing(2)
    title_lbl = QLabel(title.upper())
    title_lbl.setStyleSheet(
        f"color:{C['txt3']};font-size:9px;font-weight:bold;letter-spacing:1px;border:none;")
    cl.addWidget(title_lbl)
    value_lbl = QLabel("—")
    size = 16 if big else 13
    value_lbl.setStyleSheet(
        f"color:{C['txt']};font-family:Consolas;font-size:{size}px;"
        f"font-weight:bold;border:none;")
    cl.addWidget(value_lbl)
    return card, value_lbl

# ── Main Window ───────────────────────────────────────────────────
