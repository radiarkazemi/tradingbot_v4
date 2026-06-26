"""
gui/panel_report.py — Report tab: trade history, stats, chart
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


class ReportPanelMixin:
    def _tab_report(self):
        """
        Report tab — fully responsive.
        Layout:
          1. Filter bar  (stretchy combos, no fixed widths)
          2. Stats cards  (2-row grid, font scales, no fixed height)
          3. Chart        (expanding, matplotlib or fallback label)
          4. Trade table  (Stretch on all key columns)
        """
        w = QWidget()
        root_vl = QVBoxLayout(w)
        root_vl.setContentsMargins(8, 8, 8, 8)
        root_vl.setSpacing(8)

        # ── 1. Filter bar ─────────────────────────────────────────
        fbar = QFrame()
        fbar.setStyleSheet(
            f"background:{C['card']};border-radius:6px;"
            f"border:1px solid {C['border']};")
        fb = QHBoxLayout(fbar)
        fb.setContentsMargins(10, 6, 10, 6)
        fb.setSpacing(8)

        lbl_per = QLabel("Period:")
        lbl_per.setStyleSheet(f"color:{C['txt2']};font-size:11px;")
        fb.addWidget(lbl_per)
        self.report_period = QComboBox()
        self.report_period.addItems([
            "Today", "Last 7 days", "Last 30 days", "This month", "All time"])
        self.report_period.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.report_period.setMinimumWidth(80)
        self.report_period.currentIndexChanged.connect(self._refresh_report)
        fb.addWidget(self.report_period)

        lbl_res = QLabel("Result:")
        lbl_res.setStyleSheet(f"color:{C['txt2']};font-size:11px;")
        fb.addWidget(lbl_res)
        self.report_result = QComboBox()
        self.report_result.addItems(
            ["All", "TP wins", "SL losses", "Risk-free", "Loss-free"])
        self.report_result.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.report_result.setMinimumWidth(70)
        self.report_result.currentIndexChanged.connect(self._refresh_report)
        fb.addWidget(self.report_result)

        refresh_btn = QPushButton("↻")
        refresh_btn.setToolTip("Refresh")
        refresh_btn.setFixedSize(30, 28)
        refresh_btn.setStyleSheet(
            f"QPushButton{{background:{C['border']};color:{C['txt']};"
            f"border:1px solid {C['border_hi']};border-radius:4px;"
            f"font-size:14px;padding:0;}}"
            f"QPushButton:hover{{background:{C['border_hi']};}}")
        refresh_btn.clicked.connect(self._refresh_report)
        fb.addWidget(refresh_btn)
        root_vl.addWidget(fbar)

        # ── 2. Stats cards — 2 rows of 3, fully responsive ───────
        # Each card uses a QVBoxLayout with word-wrap label + value.
        # No fixed heights — cards grow with font/content.
        self._report_cards = {}
        card_defs = [
            ("total",      "Trades",    C["txt2"]),
            ("wins",       "TP Wins",   C["green"]),
            ("losses",     "SL Losses", C["red"]),
            ("win_rate",   "Win Rate",  C["cyan"]),
            ("total_pnl",  "P&L ($)",   C["gold"]),
            ("total_pips", "Pips",      C["orange"]),
        ]
        grid_widget = QWidget()
        grid_widget.setStyleSheet("background:transparent;")
        from PyQt5.QtWidgets import QGridLayout
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        # Make columns equal-width and stretchy
        for col in range(3):
            grid.setColumnStretch(col, 1)

        for idx, (key, label, color) in enumerate(card_defs):
            row_i = idx // 3
            col_i = idx % 3
            card = QFrame()
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            card.setStyleSheet(
                f"QFrame{{background:{C['card']};border-radius:6px;"
                f"border:1px solid {C['border']};}}")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(2)

            lbl_title = QLabel(label)
            lbl_title.setStyleSheet(
                f"color:{C['txt3']};font-size:10px;"
                f"font-weight:bold;letter-spacing:0.5px;")
            lbl_title.setWordWrap(True)
            cl.addWidget(lbl_title)

            lbl_val = QLabel("—")
            lbl_val.setStyleSheet(
                f"color:{color};font-size:20px;font-weight:bold;")
            lbl_val.setWordWrap(True)
            lbl_val.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            cl.addWidget(lbl_val)

            grid.addWidget(card, row_i, col_i)
            self._report_cards[key] = lbl_val

        root_vl.addWidget(grid_widget)

        # ── 3. Chart ──────────────────────────────────────────────
        try:
            from matplotlib.backends.backend_qt5agg import (
                FigureCanvasQTAgg as FigureCanvas)
            from matplotlib.figure import Figure
            import matplotlib
            matplotlib.rcParams.update({
                "figure.facecolor": "#0D1117",
                "axes.facecolor":   "#161B22",
                "axes.edgecolor":   "#2A3550",
                "axes.labelcolor":  "#8B9BB4",
                "xtick.color":      "#4A5568",
                "ytick.color":      "#4A5568",
                "text.color":       "#E8EDF5",
                "grid.color":       "#2A3550",
                "grid.linestyle":   "--",
                "grid.alpha":       0.5,
            })
            self._report_fig = Figure(tight_layout=True)
            self._report_canvas = FigureCanvas(self._report_fig)
            self._report_canvas.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._report_canvas.setMinimumHeight(140)
            self._report_canvas.setMaximumHeight(260)
            self._has_chart = True
        except Exception:
            self._has_chart = False
            self._report_canvas = QLabel(
                "📊  No chart — run:  pip install matplotlib")
            self._report_canvas.setStyleSheet(
                f"color:{C['txt3']};font-size:11px;"
                f"background:{C['card']};border-radius:4px;padding:16px;"
                f"border:1px solid {C['border']};")
            self._report_canvas.setAlignment(Qt.AlignCenter)
            self._report_canvas.setMinimumHeight(80)

        root_vl.addWidget(self._report_canvas, 2)

        # ── 4. Trade history table ────────────────────────────────
        tbl_lbl = QLabel("  Trade History")
        tbl_lbl.setStyleSheet(
            f"color:{C['txt2']};font-size:11px;font-weight:bold;"
            f"background:{C['panel']};border-radius:4px;padding:4px 8px;"
            f"border:1px solid {C['border']};")
        root_vl.addWidget(tbl_lbl)

        self.report_table = QTableWidget(0, 8)
        self.report_table.setHorizontalHeaderLabels([
            "Closed", "Side", "Lot", "Entry", "Exit", "P&L", "Pips", "Result"
        ])
        self.report_table.verticalHeader().setVisible(False)
        self.report_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.report_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.report_table.setAlternatingRowColors(True)
        self.report_table.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding)

        rh = self.report_table.horizontalHeader()
        # All columns stretch proportionally — no fixed widths
        for col in range(8):
            rh.setSectionResizeMode(col, QHeaderView.Stretch)
        # Override a few that have known content widths
        rh.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Side
        rh.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Lot
        rh.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # Result

        rh.setMinimumSectionSize(40)
        self.report_table.verticalHeader().setDefaultSectionSize(24)
        self.report_table.setStyleSheet(
            f"QTableWidget{{background:{C['input']};color:{C['txt']};"
            f"gridline-color:{C['border']};border:1px solid {C['border']};"
            f"border-radius:4px;font-size:11px;}}"
            f"QTableWidget::item{{padding:3px 6px;}}"
            f"QTableWidget::item:selected{{background:{C['border_hi']};}}"
            f"QHeaderView::section{{background:{C['panel']};color:{C['txt2']};"
            f"border:none;border-right:1px solid {C['border']};"
            f"border-bottom:1px solid {C['border']};"
            f"padding:4px 6px;font-size:10px;font-weight:bold;}}"
            f"QTableWidget::item:alternate{{background:{C['card']};}}"
        )
        root_vl.addWidget(self.report_table, 3)
        return w

    def _refresh_report(self):
        """Reload trades from DB and repopulate the Report tab."""
        from datetime import date, timedelta
        period_idx = self.report_period.currentIndex()
        today = date.today()

        if period_idx == 0:   # Today
            date_from = str(today)
            date_to = str(today)
        elif period_idx == 1:  # Last 7 days
            date_from = str(today - timedelta(days=7))
            date_to = str(today)
        elif period_idx == 2:  # Last 30 days
            date_from = str(today - timedelta(days=30))
            date_to = str(today)
        elif period_idx == 3:  # This month
            date_from = str(today.replace(day=1))
            date_to = str(today)
        else:                 # All time
            date_from = None
            date_to = None

        result_map = {
            0: None, 1: "tp", 2: "sl", 3: "risk_free", 4: "loss_free"
        }
        result_filter = result_map.get(self.report_result.currentIndex())

        sym = self.sym_combo.currentText().strip() if hasattr(self, "sym_combo") else None

        trades = trade_db.query_trades(
            symbol=sym or None,
            date_from=date_from,
            date_to=date_to,
            result=result_filter,
            limit=500,
        )

        # Stats
        stats = trade_db.summary_stats(sym or None)
        total = stats.get("total") or 0
        wins = stats.get("wins") or 0
        losses = stats.get("losses") or 0
        pnl = stats.get("total_pnl") or 0.0
        pips = stats.get("total_pips") or 0.0
        win_rate = f"{wins/total*100:.0f}%" if total else "—"

        self._report_cards["total"].setText(str(total))
        self._report_cards["wins"].setText(str(wins))
        self._report_cards["losses"].setText(str(losses))
        self._report_cards["win_rate"].setText(win_rate)
        pnl_col = C["green"] if pnl >= 0 else C["red"]
        self._report_cards["total_pnl"].setStyleSheet(
            f"color:{pnl_col};font-size:20px;font-weight:bold;")
        self._report_cards["total_pnl"].setText(
            f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}")
        pips_col = C["green"] if pips >= 0 else C["red"]
        self._report_cards["total_pips"].setStyleSheet(
            f"color:{pips_col};font-size:20px;font-weight:bold;")
        self._report_cards["total_pips"].setText(
            f"+{pips:.1f}" if pips >= 0 else f"{pips:.1f}")

        # Chart
        if self._has_chart:
            self._draw_report_chart(sym, date_from)

        # Table
        result_colors = {
            "tp":         C["green"],
            "sl":         C["red"],
            "risk_free":  C["cyan"],
            "loss_free":  C["cyan"],
            "balance_tp": C["gold"],
            "manual":     C["txt2"],
        }
        self.report_table.setRowCount(len(trades))
        for row, t in enumerate(trades):
            result = t.get("result", "")
            rc = result_colors.get(result, C["txt2"])
            side = t.get("side", "")
            pnl_val = t.get("pnl") or 0.0
            pips_val = t.get("pips") or 0.0

            vals = [
                (str(t.get("close_time", ""))[:19], C["txt2"]),
                (side.upper(),
                 C["green"] if side == "buy" else C["red"]),
                (f"{t.get('lot') or 0:.2f}", C["txt"]),
                (f"{t.get('entry_price') or 0:.4f}", C["txt"]),
                (f"{t.get('exit_price') or 0:.4f}", C["txt"]),
                (f"{pnl_val:+.2f}",
                 C["green"] if pnl_val >= 0 else C["red"]),
                (f"{pips_val:+.1f}",
                 C["green"] if pips_val >= 0 else C["red"]),
                (result.upper().replace("_", "-"), rc),
            ]
            for col_i, (v, clr) in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setForeground(QColor(clr))
                if col_i in (5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.report_table.setItem(row, col_i, it)

    def _draw_report_chart(self, symbol, date_from):
        """Draw daily P&L bar chart using matplotlib."""
        try:
            days_back = {
                0: 1, 1: 7, 2: 30, 3: 30, 4: 90
            }.get(self.report_period.currentIndex(), 30)

            daily = trade_db.summary_by_day(symbol=symbol, days=days_back)
            self._report_fig.clear()

            if not daily:
                ax = self._report_fig.add_subplot(111)
                ax.text(0.5, 0.5, "No trade data for this period",
                        ha="center", va="center", fontsize=11,
                        color="#4A5568", transform=ax.transAxes)
                ax.set_axis_off()
                self._report_canvas.draw()
                return

            days = [d["day"] for d in daily]
            pnls = [d["pnl"] or 0.0 for d in daily]
            colors = ["#00D97E" if p >= 0 else "#FF4560" for p in pnls]

            ax = self._report_fig.add_subplot(111)
            bars = ax.bar(range(len(days)), pnls, color=colors,
                          width=0.6, zorder=2)
            ax.axhline(0, color="#4A5568", linewidth=0.8, zorder=1)
            ax.set_xticks(range(len(days)))
            ax.set_xticklabels(
                [d[5:] for d in days],   # MM-DD
                rotation=45, ha="right", fontsize=9)
            ax.set_ylabel("P&L ($)", fontsize=9)
            ax.set_title("Daily P&L", fontsize=10, color="#E8EDF5")
            ax.grid(axis="y", zorder=0)

            # Cumulative line on secondary axis
            if len(pnls) > 1:
                import itertools
                cumulative = list(itertools.accumulate(pnls))
                ax2 = ax.twinx()
                ax2.plot(range(len(days)), cumulative,
                         color="#F5A623", linewidth=1.5,
                         marker="o", markersize=3, zorder=3)
                ax2.set_ylabel("Cumulative ($)", fontsize=9,
                               color="#F5A623")
                ax2.tick_params(axis="y", colors="#F5A623", labelsize=8)
                ax2.spines["right"].set_color("#F5A623")

            self._report_canvas.draw()
        except Exception as e:
            import logging
            logging.getLogger("gui").debug("Chart draw error: %s", e)

    # ── Right Panel ───────────────────────────────────────────────
