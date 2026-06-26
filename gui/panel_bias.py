"""
gui/panel_bias.py — Bias tab: ICT multi-TF bias table and signal breakdown
Mixin class — combined into GUI via multiple inheritance in gui_pkg/app.py.
All methods receive `self` which is the full GUI instance.
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


class BiasPanelMixin:
    def _tab_bias(self):
        """
        Right-panel Bias tab.
        Top half: 5-row table (one row per TF M1→H1).
        Bottom half: signal detail panel for the selected row.
        Updates via _on_bias_update() which is called whenever
        BiasWatcher emits new results.
        """
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(6, 6, 6, 6)
        vl.setSpacing(6)

        # ── Top bar: last-updated label + manual refresh ──────────
        top_row = QHBoxLayout()
        self.bias_updated_lbl = QLabel("🧭  ICT Bias — not running")
        self.bias_updated_lbl.setStyleSheet(
            f"color:{C['txt3']};font-size:11px;")
        top_row.addWidget(self.bias_updated_lbl)
        top_row.addStretch()

        # Dominant direction badge
        self.bias_dominant_lbl = QLabel("")
        self.bias_dominant_lbl.setFixedHeight(24)
        self.bias_dominant_lbl.setAlignment(Qt.AlignCenter)
        self.bias_dominant_lbl.setStyleSheet(
            f"background:{C['card']};border-radius:4px;"
            f"padding:2px 10px;font-size:12px;font-weight:bold;"
            f"color:{C['txt2']};")
        top_row.addWidget(self.bias_dominant_lbl)
        vl.addLayout(top_row)

        # ── Main table: one row per TF ────────────────────────────
        # Columns: TF | Direction | Bull% | Bear% | Bar | Confidence | Score
        self.bias_table = QTableWidget(5, 7)
        self.bias_table.setHorizontalHeaderLabels([
            "TF", "Bias", "🟢 Bull", "🔴 Bear", "Strength", "Confidence", "Score"
        ])
        self.bias_table.verticalHeader().setVisible(False)
        self.bias_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.bias_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bias_table.setAlternatingRowColors(True)
        self.bias_table.setMinimumHeight(160)
        self.bias_table.setMaximumHeight(185)
        hdr = self.bias_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        self.bias_table.setColumnWidth(0, 46)
        self.bias_table.setColumnWidth(1, 70)
        self.bias_table.setColumnWidth(2, 56)
        self.bias_table.setColumnWidth(3, 56)
        self.bias_table.setColumnWidth(5, 76)
        self.bias_table.setColumnWidth(6, 56)
        self.bias_table.setStyleSheet(
            f"QTableWidget {{ background:{C['input']}; color:{C['txt']};"
            f"gridline-color:{C['border']};border:none;font-size:12px; }}"
            f"QTableWidget::item:selected {{ background:{C['border_hi']}; }}"
            f"QHeaderView::section {{ background:{C['panel']};color:{C['txt2']};"
            f"border:none;border-bottom:1px solid {C['border']};padding:3px;"
            f"font-size:11px; }}"
            f"QTableWidget::item:alternate {{ background:{C['card']}; }}"
        )
        self.bias_table.selectionModel().selectionChanged.connect(
            self._on_bias_row_selected)
        vl.addWidget(self.bias_table)

        # ── Signal detail panel ───────────────────────────────────
        detail_lbl = QLabel("  Signal breakdown  (click a row above)")
        detail_lbl.setStyleSheet(
            f"color:{C['txt2']};font-size:11px;font-weight:bold;"
            f"background:{C['panel']};border-radius:4px;padding:4px 8px;")
        vl.addWidget(detail_lbl)

        self.bias_signal_table = QTableWidget(0, 4)
        self.bias_signal_table.setHorizontalHeaderLabels([
            "Signal", "Vote", "Weight", "Reason"
        ])
        self.bias_signal_table.verticalHeader().setVisible(False)
        self.bias_signal_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bias_signal_table.setSelectionMode(QTableWidget.NoSelection)
        sh = self.bias_signal_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.Fixed)
        sh.setSectionResizeMode(1, QHeaderView.Fixed)
        sh.setSectionResizeMode(2, QHeaderView.Fixed)
        sh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.bias_signal_table.setColumnWidth(0, 130)
        self.bias_signal_table.setColumnWidth(1, 54)
        self.bias_signal_table.setColumnWidth(2, 54)
        self.bias_signal_table.setStyleSheet(
            f"QTableWidget {{ background:{C['input']}; color:{C['txt']};"
            f"gridline-color:{C['border']};border:none;font-size:11px; }}"
            f"QHeaderView::section {{ background:{C['panel']};color:{C['txt2']};"
            f"border:none;border-bottom:1px solid {C['border']};padding:3px;"
            f"font-size:11px; }}"
        )
        vl.addWidget(self.bias_signal_table, 1)

        # ── Alignment hint at bottom ──────────────────────────────
        hint = QLabel(
            "🟢 Bull ≥62%   🔴 Bear ≥62%   ⚪ Neutral   |   "
            "Strong ≥40pt spread   Moderate ≥20pt   Weak <20pt"
        )
        hint.setStyleSheet(f"color:{C['txt3']};font-size:10px;")
        hint.setWordWrap(True)
        vl.addWidget(hint)

        # Internal storage
        self._bias_latest: dict = {}
        return w

    def _on_bias_update(self, results: dict):
        """
        Receive fresh bias results from BiasWatcher and repopulate
        the table. Called on the Qt main thread via signal.
        """
        self._bias_latest = results
        from datetime import datetime as _dt
        now = _dt.now().strftime("%H:%M:%S")
        self.bias_updated_lbl.setText(f"🧭  Last updated: {now}")

        # Dominant direction badge
        bull = sum(1 for b in results.values() if b.direction == "BULL")
        bear = sum(1 for b in results.values() if b.direction == "BEAR")
        neut = sum(1 for b in results.values() if b.direction == "NEUTRAL")
        if bull > bear and bull > neut:
            dom, col = f"🟢  BULLISH  ({bull}/5 TFs)", C["green"]
        elif bear > bull and bear > neut:
            dom, col = f"🔴  BEARISH  ({bear}/5 TFs)", C["red"]
        else:
            dom, col = f"⚪  MIXED  ({bull}🟢 {bear}🔴 {neut}⚪)", C["txt2"]

        self.bias_dominant_lbl.setText(dom)
        self.bias_dominant_lbl.setStyleSheet(
            f"background:{C['card']};border-radius:4px;"
            f"padding:2px 14px;font-size:12px;font-weight:bold;"
            f"color:{col};border:1px solid {col}40;")

        tf_order = ["M1", "M5", "M15", "M30", "H1"]
        for row, tf_name in enumerate(tf_order):
            b = results.get(tf_name)
            if b is None:
                for col_i in range(7):
                    self.bias_table.setItem(
                        row, col_i, QTableWidgetItem("—"))
                continue

            # TF name
            tf_item = QTableWidgetItem(tf_name)
            tf_item.setTextAlignment(Qt.AlignCenter)
            tf_item.setForeground(QColor(C["gold"]))
            self.bias_table.setItem(row, 0, tf_item)

            # Direction with emoji
            if b.direction == "BULL":
                dir_text, dir_col = "🟢 BULL", C["green"]
            elif b.direction == "BEAR":
                dir_text, dir_col = "🔴 BEAR", C["red"]
            else:
                dir_text, dir_col = "⚪ NEUT", C["txt2"]
            dir_item = QTableWidgetItem(dir_text)
            dir_item.setTextAlignment(Qt.AlignCenter)
            dir_item.setForeground(QColor(dir_col))
            self.bias_table.setItem(row, 1, dir_item)

            # Bull %
            bull_item = QTableWidgetItem(f"{b.bull_pct:.1f}%")
            bull_item.setTextAlignment(Qt.AlignCenter)
            bull_item.setForeground(QColor(
                C["green"] if b.bull_pct >= 55 else C["txt2"]))
            self.bias_table.setItem(row, 2, bull_item)

            # Bear %
            bear_item = QTableWidgetItem(f"{b.bear_pct:.1f}%")
            bear_item.setTextAlignment(Qt.AlignCenter)
            bear_item.setForeground(QColor(
                C["red"] if b.bear_pct >= 55 else C["txt2"]))
            self.bias_table.setItem(row, 3, bear_item)

            # Strength bar (text-based progress)
            spread = abs(b.bull_pct - b.bear_pct)
            filled = int(spread / 5)   # 0..20 blocks
            bar = "█" * filled + "░" * (20 - filled)
            bar_item = QTableWidgetItem(f"{bar}  {spread:.0f}pt")
            bar_item.setForeground(QColor(
                C["green"] if b.direction == "BULL" else
                C["red"] if b.direction == "BEAR" else C["txt3"]))
            self.bias_table.setItem(row, 4, bar_item)

            # Confidence
            conf_item = QTableWidgetItem(b.confidence)
            conf_item.setTextAlignment(Qt.AlignCenter)
            conf_col = (C["green"] if b.confidence == "Strong" else
                        C["gold"] if b.confidence == "Moderate" else C["txt3"])
            conf_item.setForeground(QColor(conf_col))
            self.bias_table.setItem(row, 5, conf_item)

            # Raw score
            score_item = QTableWidgetItem(f"{b.score:+.2f}")
            score_item.setTextAlignment(Qt.AlignCenter)
            score_item.setForeground(QColor(
                C["green"] if b.score > 0 else
                C["red"] if b.score < 0 else C["txt2"]))
            self.bias_table.setItem(row, 6, score_item)

        # Re-select current row to refresh signal detail
        rows = self.bias_table.selectionModel().selectedRows()
        if rows:
            self._on_bias_row_selected()
        elif results:
            self.bias_table.selectRow(0)

    def _on_bias_row_selected(self):
        """Populate signal breakdown table from the selected TF row."""
        rows = self.bias_table.selectionModel().selectedRows()
        if not rows:
            return
        tf_order = ["M1", "M5", "M15", "M30", "H1"]
        tf_name = tf_order[rows[0].row()]
        b = self._bias_latest.get(tf_name)
        if b is None:
            self.bias_signal_table.setRowCount(0)
            return

        self.bias_signal_table.setRowCount(len(b.signals))
        for i, sig in enumerate(b.signals):
            # Signal name
            name_item = QTableWidgetItem(sig.name)
            self.bias_signal_table.setItem(i, 0, name_item)

            # Vote with direction arrow
            if sig.vote > 0.05:
                vote_str, vote_col = f"▲ +{sig.vote:.2f}", C["green"]
            elif sig.vote < -0.05:
                vote_str, vote_col = f"▼ {sig.vote:.2f}", C["red"]
            else:
                vote_str, vote_col = f"● {sig.vote:.2f}", C["txt3"]
            vote_item = QTableWidgetItem(vote_str)
            vote_item.setTextAlignment(Qt.AlignCenter)
            vote_item.setForeground(QColor(vote_col))
            self.bias_signal_table.setItem(i, 1, vote_item)

            # Weight
            w_item = QTableWidgetItem(f"{sig.weight:.1f}x")
            w_item.setTextAlignment(Qt.AlignCenter)
            w_item.setForeground(QColor(C["gold"]))
            self.bias_signal_table.setItem(i, 2, w_item)

            # Reason
            reason_item = QTableWidgetItem(sig.reason)
            reason_item.setForeground(QColor(C["txt2"]))
            self.bias_signal_table.setItem(i, 3, reason_item)

        self.bias_signal_table.resizeRowsToContents()
