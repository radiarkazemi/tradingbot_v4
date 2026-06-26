"""
gui/panel_right.py — Right panel shell + Log, Sources, Orders tabs.
Mixin — combined into GUI in gui_pkg/app.py.
"""

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QTextEdit, QFrame,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QDoubleSpinBox, QSpinBox, QComboBox, QSplitter, QSizePolicy,
    QProgressBar, QCheckBox, QScrollArea, QLineEdit,
    QSystemTrayIcon, QMenu, QAction, QGridLayout,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QLinearGradient, QPen
from .theme import C, SS
from .widgets import Sig, Sparkline, _stat_card, _vline, _hline
from .shared_imports import *


class RightPanelMixin:
    def _build_right(self):
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setSpacing(0)
        vl.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_log(),       "📋  Log")
        self.tabs.addTab(self._tab_sources(),   "📌  Sources")
        self.tabs.addTab(self._tab_orders(),    "📊  Orders")
        self.tabs.addTab(self._tab_bias(),      "🧭  Bias")
        self.tabs.addTab(self._tab_report(),    "📈  Report")
        vl.addWidget(self.tabs)
        return w

    def _tab_log(self):
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.setSpacing(4)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        vl.addWidget(self.log_view)
        btn = QPushButton("Clear Log")
        btn.setFixedHeight(24)
        btn.clicked.connect(self.log_view.clear)
        vl.addWidget(btn, alignment=Qt.AlignRight)
        return w

    def _tab_sources(self):
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(6, 6, 6, 6)
        vl.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._src_cards = {}
        for key, label, color in [
            ("total",     "RECTS",      C['cyan']),
            ("idle",      "IDLE",       C['txt2']),
            ("pending",   "PENDING",    C['orange']),
            ("active",    "ACTIVE",     C['green']),
            ("exhausted", "EXHAUSTED",  C['red']),
        ]:
            f = QFrame()
            f.setStyleSheet(
                f"background:{C['card']};border:1px solid {C['border']};border-radius:6px;")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(8, 4, 8, 4)
            fv.setSpacing(0)
            lt = QLabel(label)
            lt.setStyleSheet(
                f"color:{C['txt3']};font-size:8px;font-weight:bold;")
            lt.setAlignment(Qt.AlignCenter)
            lv = QLabel("0")
            lv.setStyleSheet(
                f"color:{color};font-size:15px;font-weight:bold;font-family:Consolas;")
            lv.setAlignment(Qt.AlignCenter)
            fv.addWidget(lt)
            fv.addWidget(lv)
            self._src_cards[key] = lv
            row.addWidget(f)
        vl.addLayout(row)

        self.src_table = QTableWidget(0, 7)
        self.src_table.setHorizontalHeaderLabels(
            ["Rect Name", "Range", "State", "Round", "Touch", "BUY Lot", "SELL Lot"])
        self.src_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 7):
            self.src_table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents)
        self.src_table.setAlternatingRowColors(True)
        self.src_table.setEditTriggers(QTableWidget.NoEditTriggers)
        vl.addWidget(self.src_table)
        return w

    def _tab_orders(self):
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(6, 6, 6, 6)
        vl.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._ord_cards = {}
        for key, label, color in [
            ("pending",   "PENDING",    C['cyan']),
            ("buy_pos",   "BUY POS",    C['green']),
            ("sell_pos",  "SELL POS",   C['red']),
            ("total_pnl", "OPEN P&L",   C['purple']),
        ]:
            f = QFrame()
            f.setStyleSheet(
                f"background:{C['card']};border:1px solid {C['border']};border-radius:6px;")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(8, 4, 8, 4)
            fv.setSpacing(0)
            lt = QLabel(label)
            lt.setStyleSheet(
                f"color:{C['txt3']};font-size:8px;font-weight:bold;")
            lt.setAlignment(Qt.AlignCenter)
            lv = QLabel("—")
            lv.setStyleSheet(
                f"color:{color};font-size:15px;font-weight:bold;font-family:Consolas;")
            lv.setAlignment(Qt.AlignCenter)
            fv.addWidget(lt)
            fv.addWidget(lv)
            self._ord_cards[key] = lv
            row.addWidget(f)
        vl.addLayout(row)

        grp_pend = QGroupBox("🔵  Pending Orders")
        pv = QVBoxLayout(grp_pend)
        pv.setContentsMargins(4, 4, 4, 4)
        self.tbl_pending = QTableWidget(0, 6)
        self.tbl_pending.setHorizontalHeaderLabels(
            ["Ticket", "Type", "Entry", "SL", "Volume", "TP"])
        self.tbl_pending.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_pending.setAlternatingRowColors(True)
        self.tbl_pending.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_pending.setMaximumHeight(160)
        pv.addWidget(self.tbl_pending)
        vl.addWidget(grp_pend)

        grp_pos = QGroupBox("🟢  Open Positions")
        posv = QVBoxLayout(grp_pos)
        posv.setContentsMargins(4, 4, 4, 4)
        self.tbl_positions = QTableWidget(0, 8)
        self.tbl_positions.setHorizontalHeaderLabels(
            ["Ticket", "Type", "Entry", "SL", "TP", "TP%", "Volume", "P&L"])
        self.tbl_positions.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_positions.setAlternatingRowColors(True)
        self.tbl_positions.setEditTriggers(QTableWidget.NoEditTriggers)
        posv.addWidget(self.tbl_positions)
        vl.addWidget(grp_pos)

        btn_ref = QPushButton("🔄  Refresh Now")
        btn_ref.setFixedHeight(26)
        btn_ref.clicked.connect(self._refresh_orders)
        vl.addWidget(btn_ref, alignment=Qt.AlignRight)
        return w

    # ── Status Bar ────────────────────────────────────────────────
