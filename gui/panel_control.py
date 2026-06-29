"""
gui/panel_control.py — Left panel, header, control tab
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


class ControlPanelMixin:

    def _toggle_btn(self, label, tooltip, color_on, handler):
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setMinimumHeight(28)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(f"""
            QPushButton {{
                background:#1A2030; color:#4A5568;
                border:1px solid #2A3550; border-radius:5px;
                font-weight:bold; font-size:11px;
                text-align:left; padding:0 0 0 8px;
            }}
            QPushButton:checked {{
                background:rgba(0,160,220,0.12);
                color:{color_on}; border:1px solid {color_on};
            }}
            QPushButton:hover:!checked {{ border-color:#3A4560; color:#8B9BB4; }}
            QPushButton:hover:checked  {{ background:rgba(0,160,220,0.18); }}
        """)
        btn.toggled.connect(handler)
        return btn

    def _btn_row(self, a, b, layout):
        r = QHBoxLayout()
        r.setSpacing(5)
        r.addWidget(a)
        r.addWidget(b)
        layout.addLayout(r)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vl = QVBoxLayout(root)
        vl.setSpacing(6)
        vl.setContentsMargins(10, 10, 10, 10)
        vl.addWidget(self._build_header())

        spl = QSplitter(Qt.Horizontal)
        spl.addWidget(self._build_left_tabs())
        spl.addWidget(self._build_right())
        spl.setSizes([330, 730])
        spl.setCollapsible(0, False)
        spl.setCollapsible(1, False)
        spl.widget(0).setMinimumWidth(300)
        vl.addWidget(spl, 1)
        vl.addWidget(self._build_statusbar())

    def _build_left_tabs(self):
        """
        Left side is itself a tabbed widget: ⚙️ Control (trading
        settings/start-stop) and 🔬 Detectors (FVG/OB/Confluence/
        MTF FVG/AMD — visualization only, no trades placed from
        here). Keeping detector settings on the left, separate from
        the right side's Log/Sources/Orders, means switching detector
        knobs never hides the live trade state you're watching.
        """
        tabs = QTabWidget()
        tabs.addTab(self._build_left(),      "⚙️  Control")
        tabs.addTab(self._tab_detectors(),   "🔬  Detectors")
        return tabs

    def _build_header(self):
        w = QFrame()
        w.setStyleSheet(
            f"background:{C['panel']};border:1px solid {C['border']};border-radius:6px;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(14, 8, 14, 8)
        t = QLabel(
            "📈  TraderBot  <span style='color:#4A5568;font-size:10px;'>v4</span>")
        t.setStyleSheet(f"color:{C['gold']};font-size:16px;font-weight:bold;")
        hl.addWidget(t)
        hl.addStretch()

        # ── Account & Settings always visible in header ──────────
        self.btn_header_settings = QPushButton("⚙  Account & Settings")
        self.btn_header_settings.setFixedHeight(28)
        self.btn_header_settings.setToolTip(
            "Update MT5 login, password, server and default preferences.")
        self.btn_header_settings.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C['txt2']};"
            f"border:1px solid {C['border']};border-radius:4px;"
            f"padding:2px 10px;font-size:11px;}}"
            f"QPushButton:hover{{color:{C['txt']};border-color:{C['border_hi']};}}"
        )
        self.btn_header_settings.clicked.connect(self._show_settings)
        hl.addWidget(self.btn_header_settings)
        self.lbl_price = QLabel("Price: —")
        self.lbl_price.setStyleSheet(
            f"color:{C['cyan']};font-family:Consolas;font-size:14px;font-weight:bold;")
        hl.addWidget(self.lbl_price)
        hl.addWidget(_vline())
        self.lbl_sym_hdr = QLabel(WATCH_SYMBOL)
        self.lbl_sym_hdr.setStyleSheet(f"color:{C['txt2']};font-size:12px;")
        hl.addWidget(self.lbl_sym_hdr)
        hl.addWidget(_vline())
        self.lbl_ea_status = QLabel("EA: —")
        self.lbl_ea_status.setStyleSheet(f"color:{C['txt3']};font-size:10px;")
        self.lbl_ea_status.setToolTip("ObjectExporter EA file status")
        hl.addWidget(self.lbl_ea_status)
        hl.addWidget(_vline())
        self.lbl_status = QLabel("⚫  Stopped")
        self.lbl_status.setStyleSheet(f"color:{C['txt2']};font-size:11px;")
        hl.addWidget(self.lbl_status)
        return w

    # ── Shared field-row helpers ─────────────────────────────────
    def _lbl(self, text, tip=""):
        l = QLabel(text)
        l.setStyleSheet(f"color:{C['txt2']};font-size:11px;")
        if tip:
            l.setToolTip(tip)
        return l

    def _row(self, label, widget, grp_layout, tip=""):
        hl = QHBoxLayout()
        hl.setSpacing(8)
        lw = self._lbl(label, tip)
        lw.setFixedWidth(100)
        hl.addWidget(lw)
        widget.setMinimumWidth(100)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        hl.addWidget(widget)
        grp_layout.addLayout(hl)

    def _section_label(self, text, color=None):
        """Small bold sub-header used to group related fields inside
        a QGroupBox without needing a nested box for every cluster."""
        l = QLabel(text)
        l.setStyleSheet(
            f"color:{color or C['txt3']};font-size:9px;font-weight:bold;"
            f"letter-spacing:1px;margin-top:2px;")
        return l

    # ── Left Panel ────────────────────────────────────────────────

    def _build_left(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setSpacing(8)
        vl.setContentsMargins(0, 0, 4, 0)
        self._left_panel_layout = vl   # used by update banner injection
        scroll.setWidget(w)

        _row, _lbl = self._row, self._lbl

        # ── Bot Control group ─────────────────────────────────────
        grp_ctrl = QGroupBox("⚙️  Bot Control")
        cl = QVBoxLayout(grp_ctrl)
        cl.setSpacing(6)

        cl.addWidget(self._section_label("CONNECTION"))
        sym_row = QHBoxLayout()
        sym_row.setSpacing(6)
        lbl_sym = self._lbl("🎯 Symbol:", "MT5 symbol to watch")
        lbl_sym.setFixedWidth(100)
        sym_row.addWidget(lbl_sym)
        self.sym_combo = QComboBox()
        self.sym_combo.setEditable(True)
        self.sym_combo.addItems([
            # LiteFinance-style ("_o" suffix) — current broker
            "EURUSD_o", "GBPUSD_o", "USDJPY_o", "USDCHF_o", "AUDUSD_o",
            "USDCAD_o", "NZDUSD_o", "XAUUSD_o", "XAGUSD_o", "BTCUSD_o",
            "ETHUSD_o", "US30_o", "NAS100_o", "US500_o", "DE40_o",
            # Bare names — other brokers / before you know the suffix
            "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "GBPJPY",
        ])
        self.sym_combo.setCurrentText(WATCH_SYMBOL)
        self.sym_combo.currentTextChanged.connect(self._on_symbol_changed)
        self.sym_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sym_row.addWidget(self.sym_combo)
        self.btn_detect_syms = QPushButton("🔄")
        self.btn_detect_syms.setFixedWidth(32)
        self.btn_detect_syms.setToolTip(
            "Pull the REAL symbol list from your connected MT5 account "
            "(fixes broker-specific suffixes like _o/_i/.a/m automatically — "
            "no more guessing the exact name)")
        self.btn_detect_syms.clicked.connect(self._detect_symbols)
        sym_row.addWidget(self.btn_detect_syms)
        cl.addLayout(sym_row)

        cl.addWidget(_hline())
        cl.addWidget(self._section_label("SIZING"))

        self.lot_mode_combo = QComboBox()
        self.lot_mode_combo.addItems([
            "Mode 1 — 0.01 then +0.01/touch (max 0.12)",
            "Mode 2 — 0.01 then +0.02/touch (max 0.22)",
            "Mode 3 — Classic Martingale (2x doubling, no touch cap)",
        ])
        self.lot_mode_combo.setCurrentIndex(
            {1: 0, 2: 1, 3: 2}.get(cfg.SOFT_LOT_MODE, 0))
        _row("🪜 Soft Lot Mode:", self.lot_mode_combo, cl,
             "Mode 1/2: lot at each touch comes from a fixed table — "
             "Mode 1 steps by 0.01/touch, Mode 2 by 0.02/touch (both cap "
             "at the 11th touch, then the kill switch fires).\n\n"
             "Mode 3: the original doubling formula (round(x×2,2)) with "
             "NO touch cap — runs until balance TP, the OB+FVG bounce-"
             "confluence gate declines to continue (kicks in at lot "
             "≥0.64), or margin can't afford the minimum lot. Higher "
             "risk ceiling than Mode 1/2 — kept for comparison/backtesting.")

        self.spin_lot = QDoubleSpinBox()
        self.spin_lot.setRange(0.01, 100.0)
        self.spin_lot.setSingleStep(0.01)
        self.spin_lot.setValue(LOT_SIZE)
        self.spin_lot.setDecimals(2)
        self.spin_lot.valueChanged.connect(self._on_base_lot_changed)
        _row("📦 Base Lot:", self.spin_lot, cl,
             "Starting lot size (\"start\", touch 0). Changing this updates the lot mode dropdown labels.")

        self.spin_balance_tp = QDoubleSpinBox()
        self.spin_balance_tp.setRange(1.0, 100.0)
        self.spin_balance_tp.setSingleStep(1.0)
        self.spin_balance_tp.setValue(10.0)
        self.spin_balance_tp.setDecimals(1)
        self.spin_balance_tp.setSuffix(" %")
        _row("💰 Balance TP (R3):", self.spin_balance_tp, cl,
             "Account-wide stop level (close all & stop here).")

        cl.addWidget(_hline())
        cl.addWidget(self._section_label("BEHAVIOR"))

        self.chk_tp_free = self._toggle_btn(
            "🚫  TP-Free",
            "Place all orders WITHOUT a take-profit.\n"
            "Positions run until manually closed or SL hit.",
            C["orange"], lambda c: None)
        self.chk_follow = self._toggle_btn(
            "📐  Follow Rect",
            "When you drag/resize a rectangle while idle,\n"
            "the bot resets and re-watches from the new edges.",
            C["cyan"], lambda c: None)
        self.chk_follow.setChecked(True)
        self._btn_row(self.chk_tp_free, self.chk_follow, cl)

        self.chk_resume = self._toggle_btn(
            "🔁  Resume Session",
            "On start, scan MT5 for existing bot positions\n"
            "and resume monitoring them without re-entering.",
            C["orange"], lambda c: None)
        cl.addWidget(self.chk_resume)

        cl.addWidget(_hline())
        cl.addWidget(self._section_label("PROTECTIONS"))

        self.chk_loss_free = self._toggle_btn(
            "🟩  Loss-Free (R1)",
            "When profit ≥ 1R: move SL to breakeven.\n"
            "This round can no longer lose.",
            C["green"], self._on_loss_free_toggled)
        self.chk_risk_free = self._toggle_btn(
            "🛡  Risk-Free (R2)",
            "When price at 2/3 of entry→TP:\n"
            "  Lock SL at 2/3 mark (2/3 of TP as profit).\n"
            "  With losses: SL covers losses×2.",
            C["cyan"], self._on_risk_free_toggled)
        self._btn_row(self.chk_loss_free, self.chk_risk_free, cl)

        self.chk_trailing = self._toggle_btn(
            "📈  Trailing SL",
            "After R2 locks the SL, trail price by\n"
            "config.TRAILING_STEP_PIPS — max profit without full TP.",
            C["cyan"], self._on_trailing_toggled)
        self.chk_partial_exit = self._toggle_btn(
            "📤  Partial Exit (R3)",
            "At TP: close 70%, keep 30% running.",
            C["gold"], self._on_partial_exit_toggled)
        self._btn_row(self.chk_trailing, self.chk_partial_exit, cl)

        self.chk_entry_filter = self._toggle_btn(
            "🟡  OB+FVG Filter",
            "Only enter if OB+FVG confluence overlaps touched edge.",
            C["gold"], self._on_entry_filter_toggled)
        cl.addWidget(self.chk_entry_filter)

        cl.addWidget(_hline())

        self.btn_start = QPushButton("▶  Start Watcher")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.clicked.connect(self._start)
        cl.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■  Stop Watcher")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        cl.addWidget(self.btn_stop)

        self.btn_settings = QPushButton("⚙  Account & Settings")
        self.btn_settings.setMinimumHeight(30)
        self.btn_settings.setToolTip(
            "Update your MT5 login, password, server, and default preferences.")
        self.btn_settings.clicked.connect(self._show_settings)
        cl.addWidget(self.btn_settings)

        vl.addWidget(grp_ctrl)

        # ── Active Sequences group ────────────────────────────────
        grp_seq = QGroupBox("🔥  Active Sequences")
        sl = QVBoxLayout(grp_seq)
        sl.setSpacing(2)
        self.lbl_sequences = QLabel("—  No active sequences")
        self.lbl_sequences.setStyleSheet(
            f"color:{C['txt2']};font-size:11px;font-family:Consolas;")
        self.lbl_sequences.setWordWrap(True)
        sl.addWidget(self.lbl_sequences)
        vl.addWidget(grp_seq)

        # ── Balance TP progress ───────────────────────────────────
        grp_bal = QGroupBox("💰  Balance Progress")
        bl = QVBoxLayout(grp_bal)
        bl.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        card_bal, self.lbl_balance = _stat_card("Balance", C['gold'], big=True)
        card_tgt, self.lbl_balance_target = _stat_card("Target", C['cyan'])
        row1.addWidget(card_bal)
        row1.addWidget(card_tgt)
        bl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        card_net, self.lbl_net_profit = _stat_card("Net Profit", C['green'])
        card_pnl, self.lbl_total_pnl_all = _stat_card("Total PnL", C['blue'])
        card_dd, self.lbl_loss_pct = _stat_card("Drawdown", C['red'])
        row2.addWidget(card_net)
        row2.addWidget(card_pnl)
        row2.addWidget(card_dd)
        bl.addLayout(row2)

        self.sparkline = Sparkline()
        self.sparkline.setToolTip(
            "Live equity trend (recent refreshes). Green = trending "
            "up, red = trending down over the visible window.")
        bl.addWidget(self.sparkline)

        vl.addWidget(grp_bal)

        # ── Cancel All group ──────────────────────────────────────
        grp_cancel = QGroupBox("🛑  Emergency")
        ecl = QVBoxLayout(grp_cancel)
        self.btn_cancel_all = QPushButton("🗑️  Cancel All Bot Orders")
        self.btn_cancel_all.setObjectName("btn_cancel")
        self.btn_cancel_all.setMinimumHeight(30)
        self.btn_cancel_all.clicked.connect(self._cancel_all)
        ecl.addWidget(self.btn_cancel_all)
        vl.addWidget(grp_cancel)

        vl.addStretch()
        return scroll

    # ── Detectors Tab (FVG / OB / Confluence / MTF FVG / AMD) ───────
    # All detector/visualization settings live here, separate from
    # the trading controls in the left sidebar — they don't affect
    # entries (manual rectangles only, see core/watcher.py) and were
    # cluttering the main control panel.
    def _build_statusbar(self):
        w = QFrame()
        w.setStyleSheet(
            f"background:{C['panel']};border:1px solid {C['border']};border-radius:4px;")
        w.setFixedHeight(28)
        hl = QHBoxLayout(w)
        hl.setContentsMargins(10, 0, 10, 0)
        self.lbl_sb = QLabel("Ready")
        self.lbl_sb.setStyleSheet(f"color:{C['txt2']};font-size:10px;")
        hl.addWidget(self.lbl_sb)
        hl.addStretch()
        self.lbl_candle = QLabel("Candle: —")
        self.lbl_candle.setStyleSheet(
            f"color:{C['txt3']};font-size:10px;font-family:Consolas;")
        hl.addWidget(self.lbl_candle)
        return w

    # ── Control Handlers ──────────────────────────────────────────

    # ── Auto-update ──────────────────────────────────────────────

    # ── Tray icon ─────────────────────────────────────────────────
