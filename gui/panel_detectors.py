"""
gui/panel_detectors.py — Detectors tab (FVG/OB/Confluence/MTF/AMD/Bias)
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


class DetectorsPanelMixin:
    def _tab_detectors(self):
        outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setSpacing(8)
        vl.setContentsMargins(6, 6, 6, 6)
        outer.setWidget(w)

        _row, _lbl = self._row, self._lbl

        hint = QLabel(
            "Detection & visualization only — none of these place trades. "
            "Entries are always manual (draw a rectangle on the chart).")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{C['txt3']};font-size:10px;font-style:italic;")
        vl.addWidget(hint)

        # ── FVG Settings group ────────────────────────────────────
        grp_fvg = QGroupBox("📐  Fair Value Gaps (FVG)")
        fv = QVBoxLayout(grp_fvg)
        fv.setSpacing(6)

        self.chk_fvg = QCheckBox("Enable FVG detection")
        self.chk_fvg.setChecked(True)
        self.chk_fvg.setToolTip(
            "Scan candles for FVG patterns and draw rectangles on chart")
        self.chk_fvg.stateChanged.connect(self._on_fvg_toggled)
        fv.addWidget(self.chk_fvg)

        fvg_gap_row = QHBoxLayout()
        fvg_gap_row.setSpacing(8)
        lbl_gap = _lbl("📏 Min Gap (pips):")
        lbl_gap.setFixedWidth(100)
        lbl_gap.setToolTip(
            "Minimum FVG size in pips.\n"
            "↑ Increase → fewer FVGs, higher quality\n"
            "↓ Decrease → more FVGs, more noise\n"
            "Recommended: 2-5 for M1, 5-15 for M15"
        )
        fvg_gap_row.addWidget(lbl_gap)
        self.spin_fvg_gap = QDoubleSpinBox()
        self.spin_fvg_gap.setRange(0.5, 200.0)
        self.spin_fvg_gap.setSingleStep(0.5)
        self.spin_fvg_gap.setValue(3.0)
        self.spin_fvg_gap.setDecimals(1)
        self.spin_fvg_gap.setSuffix(" pips")
        self.spin_fvg_gap.valueChanged.connect(self._on_fvg_settings_changed)
        fvg_gap_row.addWidget(self.spin_fvg_gap)
        fv.addLayout(fvg_gap_row)

        fvg_lb_row = QHBoxLayout()
        fvg_lb_row.setSpacing(8)
        lbl_lb = _lbl("🕯 Lookback:")
        lbl_lb.setFixedWidth(100)
        lbl_lb.setToolTip("How many candles to scan for FVGs")
        fvg_lb_row.addWidget(lbl_lb)
        self.spin_fvg_lookback = QSpinBox()
        self.spin_fvg_lookback.setRange(10, 1000)
        self.spin_fvg_lookback.setSingleStep(50)
        self.spin_fvg_lookback.setValue(200)
        self.spin_fvg_lookback.valueChanged.connect(
            self._on_fvg_settings_changed)
        fvg_lb_row.addWidget(self.spin_fvg_lookback)
        fv.addLayout(fvg_lb_row)

        fvg_max_row = QHBoxLayout()
        fvg_max_row.setSpacing(8)
        lbl_max = _lbl("🔲 Max Rects:")
        lbl_max.setFixedWidth(100)
        lbl_max.setToolTip(
            "Maximum FVG rectangles drawn on chart (newest first)")
        fvg_max_row.addWidget(lbl_max)
        self.spin_fvg_max = QSpinBox()
        self.spin_fvg_max.setRange(1, 200)
        self.spin_fvg_max.setSingleStep(5)
        self.spin_fvg_max.setValue(30)
        self.spin_fvg_max.valueChanged.connect(self._on_fvg_settings_changed)
        fvg_max_row.addWidget(self.spin_fvg_max)
        fv.addLayout(fvg_max_row)

        self.lbl_fvg_count = QLabel("FVGs: —")
        self.lbl_fvg_count.setStyleSheet(
            f"color:{C['cyan']};font-family:Consolas;font-size:10px;")
        fv.addWidget(self.lbl_fvg_count)

        vl.addWidget(grp_fvg)

        # ── Rectangle Suggestions ──────────────────────────────────
        grp_rectsug = QGroupBox("🟧  Rectangle Suggestions (15M+5M+1M)")
        rs = QVBoxLayout(grp_rectsug)
        rs.setSpacing(6)

        hint_rs = QLabel(
            "Suggests where/how big to draw a rectangle, based on recent "
            "consolidation boxes on 15M, 5M, AND 1M simultaneously — all "
            "three are independent sources, not a 3-way confluence "
            "requirement. Visualization only, never places an order.")
        hint_rs.setWordWrap(True)
        hint_rs.setStyleSheet(
            f"color:{C['txt3']};font-size:9px;font-style:italic;")
        rs.addWidget(hint_rs)

        self.chk_rectsug = QCheckBox("Enable rectangle suggestions")
        self.chk_rectsug.setChecked(False)
        self.chk_rectsug.setToolTip(
            "Scan 15M, 5M, and 1M candles for consolidation/compression "
            "boxes and draw them as orange, unfilled suggestion "
            "rectangles on the chart. Border width shows which "
            "timeframe found each box (thicker = larger timeframe).")
        self.chk_rectsug.stateChanged.connect(self._on_rectsug_toggled)
        rs.addWidget(self.chk_rectsug)

        rectsug_bars_row = QHBoxLayout()
        rectsug_bars_row.setSpacing(8)
        lbl_rs_bars = _lbl("🕯 Min Bars:")
        lbl_rs_bars.setFixedWidth(100)
        lbl_rs_bars.setToolTip(
            "Minimum candles wide for a consolidation to count — applies "
            "independently on each of 15M/5M/1M (so e.g. 6 bars means "
            "something very different in duration on 15M vs 1M, which "
            "is the point of scanning all three)")
        rectsug_bars_row.addWidget(lbl_rs_bars)
        self.spin_rectsug_bars = QSpinBox()
        self.spin_rectsug_bars.setRange(2, 50)
        self.spin_rectsug_bars.setSingleStep(1)
        self.spin_rectsug_bars.setValue(6)
        self.spin_rectsug_bars.valueChanged.connect(
            self._on_rectsug_settings_changed)
        rectsug_bars_row.addWidget(self.spin_rectsug_bars)
        rs.addLayout(rectsug_bars_row)

        rectsug_range_row = QHBoxLayout()
        rectsug_range_row.setSpacing(8)
        lbl_rs_range = _lbl("📏 Max Range:")
        lbl_rs_range.setFixedWidth(100)
        lbl_rs_range.setToolTip(
            "Box height tolerance, as a multiple of the symbol's recent "
            "average candle range — not a fixed pip value, so this scales "
            "correctly across symbols (e.g. EURUSD vs XAUUSD).\n"
            "↓ Decrease → tighter, fewer boxes\n"
            "↑ Increase → looser, more boxes")
        rectsug_range_row.addWidget(lbl_rs_range)
        self.spin_rectsug_range = QDoubleSpinBox()
        self.spin_rectsug_range.setRange(0.5, 5.0)
        self.spin_rectsug_range.setSingleStep(0.1)
        self.spin_rectsug_range.setValue(1.5)
        self.spin_rectsug_range.setDecimals(1)
        self.spin_rectsug_range.setSuffix(" ×avg")
        self.spin_rectsug_range.valueChanged.connect(
            self._on_rectsug_settings_changed)
        rectsug_range_row.addWidget(self.spin_rectsug_range)
        rs.addLayout(rectsug_range_row)

        rectsug_max_row = QHBoxLayout()
        rectsug_max_row.setSpacing(8)
        lbl_rs_max = _lbl("🔲 Max Boxes:")
        lbl_rs_max.setFixedWidth(100)
        lbl_rs_max.setToolTip(
            "Maximum suggestion boxes drawn on chart (newest first)")
        rectsug_max_row.addWidget(lbl_rs_max)
        self.spin_rectsug_max = QSpinBox()
        self.spin_rectsug_max.setRange(1, 50)
        self.spin_rectsug_max.setSingleStep(1)
        self.spin_rectsug_max.setValue(10)
        self.spin_rectsug_max.valueChanged.connect(
            self._on_rectsug_settings_changed)
        rectsug_max_row.addWidget(self.spin_rectsug_max)
        rs.addLayout(rectsug_max_row)

        self.lbl_rectsug_count = QLabel("Suggestions: —")
        self.lbl_rectsug_count.setStyleSheet(
            f"color:{C['orange']};font-family:Consolas;font-size:10px;")
        rs.addWidget(self.lbl_rectsug_count)

        vl.addWidget(grp_rectsug)

        # ── OB Settings group ─────────────────────────────────────
        grp_ob = QGroupBox("🟦  Order Blocks (OB)")
        ov = QVBoxLayout(grp_ob)
        ov.setSpacing(6)

        self.chk_ob = QCheckBox("Enable OB detection")
        self.chk_ob.setChecked(True)
        self.chk_ob.setToolTip(
            "Scan candles for Order Block patterns\n"
            "Classic: last opposite candle before strong impulse\n"
            "BOS-based: candle that caused structure break\n"
            "Aqua = Bullish OB  |  Magenta = Bearish OB\n"
            "Mitigated zones auto-removed from chart"
        )
        self.chk_ob.stateChanged.connect(self._on_ob_toggled)
        ov.addWidget(self.chk_ob)

        ob_imp_row = QHBoxLayout()
        ob_imp_row.setSpacing(8)
        lbl_imp = _lbl("📏 Min Impulse:")
        lbl_imp.setFixedWidth(100)
        lbl_imp.setToolTip(
            "Minimum impulse to confirm an OB.\n"
            "Both conditions required:\n"
            "  1. Close beyond OB candle high/low\n"
            "  2. Move ≥ this many pips from OB boundary\n"
            "Recommended: 2-5 for M1, 5-15 for M15"
        )
        ob_imp_row.addWidget(lbl_imp)
        self.spin_ob_impulse = QDoubleSpinBox()
        self.spin_ob_impulse.setRange(0.5, 200.0)
        self.spin_ob_impulse.setSingleStep(0.5)
        self.spin_ob_impulse.setValue(3.0)
        self.spin_ob_impulse.setDecimals(1)
        self.spin_ob_impulse.setSuffix(" pips")
        self.spin_ob_impulse.valueChanged.connect(self._on_ob_settings_changed)
        ob_imp_row.addWidget(self.spin_ob_impulse)
        ov.addLayout(ob_imp_row)

        ob_lb_row = QHBoxLayout()
        ob_lb_row.setSpacing(8)
        lbl_ob_lb = _lbl("🕯 Lookback:")
        lbl_ob_lb.setFixedWidth(100)
        lbl_ob_lb.setToolTip("How many candles to scan for OB patterns")
        ob_lb_row.addWidget(lbl_ob_lb)
        self.spin_ob_lookback = QSpinBox()
        self.spin_ob_lookback.setRange(10, 1000)
        self.spin_ob_lookback.setSingleStep(50)
        self.spin_ob_lookback.setValue(200)
        self.spin_ob_lookback.valueChanged.connect(
            self._on_ob_settings_changed)
        ob_lb_row.addWidget(self.spin_ob_lookback)
        ov.addLayout(ob_lb_row)

        ob_sw_row = QHBoxLayout()
        ob_sw_row.setSpacing(8)
        lbl_ob_sw = _lbl("📐 Swing Bars:")
        lbl_ob_sw.setFixedWidth(100)
        lbl_ob_sw.setToolTip(
            "Bars each side to confirm a swing high/low.\n"
            "Used by the BOS-based OB method only.\n"
            "Higher = fewer, stronger swing points"
        )
        ob_sw_row.addWidget(lbl_ob_sw)
        self.spin_ob_swing = QSpinBox()
        self.spin_ob_swing.setRange(2, 20)
        self.spin_ob_swing.setSingleStep(1)
        self.spin_ob_swing.setValue(5)
        self.spin_ob_swing.valueChanged.connect(self._on_ob_settings_changed)
        ob_sw_row.addWidget(self.spin_ob_swing)
        ov.addLayout(ob_sw_row)

        ob_max_row = QHBoxLayout()
        ob_max_row.setSpacing(8)
        lbl_ob_max = _lbl("🔲 Max Rects:")
        lbl_ob_max.setFixedWidth(100)
        lbl_ob_max.setToolTip(
            "Maximum OB rectangles drawn on chart (newest first)")
        ob_max_row.addWidget(lbl_ob_max)
        self.spin_ob_max = QSpinBox()
        self.spin_ob_max.setRange(1, 200)
        self.spin_ob_max.setSingleStep(5)
        self.spin_ob_max.setValue(20)
        self.spin_ob_max.valueChanged.connect(self._on_ob_settings_changed)
        ob_max_row.addWidget(self.spin_ob_max)
        ov.addLayout(ob_max_row)

        self.lbl_ob_count = QLabel("OBs: —")
        self.lbl_ob_count.setStyleSheet(
            f"color:{C['aqua']};font-family:Consolas;font-size:10px;")
        ov.addWidget(self.lbl_ob_count)

        vl.addWidget(grp_ob)

        # ── Confluence Settings ───────────────────────────────────
        grp_conf = QGroupBox("🟡  OB + FVG Confluence")
        grp_conf.setStyleSheet(
            f"QGroupBox {{ background:{C['card']};border:1px solid {C['yellow']};"
            f"border-radius:6px;margin-top:14px;padding:8px 6px 6px 6px;"
            f"font-size:10px;font-weight:bold;color:{C['yellow']}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:10px;padding:0 4px; }}"
        )
        cv = QVBoxLayout(grp_conf)
        cv.setSpacing(6)

        self.chk_confluence = QCheckBox("Enable OB+FVG Confluence mode")
        self.chk_confluence.setChecked(False)
        self.chk_confluence.setStyleSheet(
            f"color:{C['yellow']};font-weight:bold;")
        self.chk_confluence.setToolTip(
            "Show only Order Blocks that have a Fair Value Gap\n"
            "appearing right after them — highest confluence zones.\n\n"
            "When ON:\n"
            "  • Individual OB and FVG rectangles are hidden\n"
            "  • Only combined confluence zones are drawn\n"
            "  • Gold outline = Bullish confluence\n"
            "  • Purple outline = Bearish confluence\n\n"
            "Requires both OB and FVG detection to be enabled."
        )
        self.chk_confluence.stateChanged.connect(self._on_confluence_toggled)
        cv.addWidget(self.chk_confluence)

        conf_win_row = QHBoxLayout()
        conf_win_row.setSpacing(8)
        lbl_conf_win = _lbl("🕯 FVG Window:")
        lbl_conf_win.setFixedWidth(100)
        lbl_conf_win.setToolTip(
            "Max candles after the OB candle for an FVG to count as confluence.\n"
            "Smaller = stricter (FVG must be right after OB)\n"
            "Larger  = looser  (FVG can be further away)\n"
            "Recommended: 5-15 for M1"
        )
        conf_win_row.addWidget(lbl_conf_win)
        self.spin_conf_window = QSpinBox()
        self.spin_conf_window.setRange(1, 100)
        self.spin_conf_window.setSingleStep(1)
        self.spin_conf_window.setValue(10)
        self.spin_conf_window.setSuffix(" bars")
        self.spin_conf_window.valueChanged.connect(
            self._on_confluence_settings_changed)
        conf_win_row.addWidget(self.spin_conf_window)
        cv.addLayout(conf_win_row)

        self.chk_conf_direction = QCheckBox("Match FVG direction to OB")
        self.chk_conf_direction.setChecked(True)
        self.chk_conf_direction.setToolTip(
            "When enabled: Bullish OB must have Bullish FVG after it (and vice versa)\n"
            "When disabled: any FVG after OB counts regardless of direction"
        )
        self.chk_conf_direction.stateChanged.connect(
            self._on_confluence_settings_changed)
        cv.addWidget(self.chk_conf_direction)

        conf_max_row = QHBoxLayout()
        conf_max_row.setSpacing(8)
        lbl_conf_max = _lbl("🔲 Max Rects:")
        lbl_conf_max.setFixedWidth(100)
        lbl_conf_max.setToolTip("Maximum confluence rectangles drawn on chart")
        conf_max_row.addWidget(lbl_conf_max)
        self.spin_conf_max = QSpinBox()
        self.spin_conf_max.setRange(1, 100)
        self.spin_conf_max.setSingleStep(5)
        self.spin_conf_max.setValue(20)
        self.spin_conf_max.valueChanged.connect(
            self._on_confluence_settings_changed)
        conf_max_row.addWidget(self.spin_conf_max)
        cv.addLayout(conf_max_row)

        self.lbl_conf_count = QLabel("Confluence: —")
        self.lbl_conf_count.setStyleSheet(
            f"color:{C['yellow']};font-family:Consolas;font-size:10px;")
        cv.addWidget(self.lbl_conf_count)

        vl.addWidget(grp_conf)

        # ── MTF FVG Confluence ────────────────────────────────────
        grp_mtf = QGroupBox("🟡  MTF FVG Confluence")
        grp_mtf.setStyleSheet(
            f"QGroupBox {{ background:{C['card']};border:1px solid {C['gold']};"
            f"border-radius:6px;margin-top:14px;padding:8px 6px 6px 6px;"
            f"font-size:10px;font-weight:bold;color:{C['gold']}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:10px;padding:0 4px; }}"
        )
        mv = QVBoxLayout(grp_mtf)
        mv.setSpacing(6)

        self.chk_mtf = QCheckBox("Enable MTF FVG detection")
        self.chk_mtf.setChecked(False)
        self.chk_mtf.setToolTip(
            "Finds price zones where the selected timeframes' FVGs\n"
            "all overlap in the same direction simultaneously.\n\n"
            "Triggered on every completed 1M candle.\n"
            "🟡 Gold box = Bullish confluence entry zone\n"
            "🟣 Purple box = Bearish confluence entry zone\n"
            "Zones auto-removed when price enters them."
        )
        self.chk_mtf.stateChanged.connect(self._on_mtf_toggled)
        mv.addWidget(self.chk_mtf)

        # ── Timeframe selection ────────────────────────────────────
        mv.addWidget(_lbl("📊 Timeframes to combine (pick 2 or 3):"))
        mtf_tf_row = QHBoxLayout()
        mtf_tf_row.setSpacing(10)
        self.chk_mtf_15m = QCheckBox("15M")
        self.chk_mtf_5m = QCheckBox("5M")
        self.chk_mtf_1m = QCheckBox("1M")
        for chk in (self.chk_mtf_15m, self.chk_mtf_5m, self.chk_mtf_1m):
            chk.setChecked(True)
            chk.stateChanged.connect(self._on_mtf_tf_selection_changed)
            mtf_tf_row.addWidget(chk)
        mv.addLayout(mtf_tf_row)

        mtf_entry_row = QHBoxLayout()
        mtf_entry_row.setSpacing(8)
        lbl_mtf_entry = _lbl("🎯 Entry TF:")
        lbl_mtf_entry.setFixedWidth(100)
        lbl_mtf_entry.setToolTip(
            "Which selected timeframe's FVG becomes the tradeable\n"
            "zone — the box price must return into for an entry\n"
            "to trigger. Doesn't have to be the smallest one."
        )
        mtf_entry_row.addWidget(lbl_mtf_entry)
        self.combo_mtf_entry = QComboBox()
        self.combo_mtf_entry.addItems(["15M", "5M", "1M"])
        self.combo_mtf_entry.setCurrentText("1M")
        self.combo_mtf_entry.currentTextChanged.connect(
            self._on_mtf_settings_changed)
        mtf_entry_row.addWidget(self.combo_mtf_entry)
        mv.addLayout(mtf_entry_row)

        mtf_gap_row = QHBoxLayout()
        mtf_gap_row.setSpacing(8)
        lbl_mtf_gap = _lbl("📏 Min Gap (pips):")
        lbl_mtf_gap.setFixedWidth(100)
        mtf_gap_row.addWidget(lbl_mtf_gap)
        self.spin_mtf_gap = QDoubleSpinBox()
        self.spin_mtf_gap.setRange(0.1, 50.0)
        self.spin_mtf_gap.setSingleStep(0.1)
        self.spin_mtf_gap.setValue(1.0)
        self.spin_mtf_gap.setDecimals(1)
        self.spin_mtf_gap.setSuffix(" pips")
        self.spin_mtf_gap.setToolTip(
            "Minimum FVG size on each timeframe to qualify")
        self.spin_mtf_gap.valueChanged.connect(self._on_mtf_settings_changed)
        mtf_gap_row.addWidget(self.spin_mtf_gap)
        mv.addLayout(mtf_gap_row)

        mtf_lb15_row = QHBoxLayout()
        mtf_lb15_row.setSpacing(8)
        lbl_mtf_lb15 = _lbl("🕯 15M Lookback:")
        lbl_mtf_lb15.setFixedWidth(100)
        mtf_lb15_row.addWidget(lbl_mtf_lb15)
        self.spin_mtf_lb15 = QSpinBox()
        self.spin_mtf_lb15.setRange(10, 200)
        self.spin_mtf_lb15.setSingleStep(10)
        self.spin_mtf_lb15.setValue(50)
        self.spin_mtf_lb15.valueChanged.connect(self._on_mtf_settings_changed)
        mtf_lb15_row.addWidget(self.spin_mtf_lb15)
        mv.addLayout(mtf_lb15_row)

        mtf_lb5_row = QHBoxLayout()
        mtf_lb5_row.setSpacing(8)
        lbl_mtf_lb5 = _lbl("🕯 5M Lookback:")
        lbl_mtf_lb5.setFixedWidth(100)
        mtf_lb5_row.addWidget(lbl_mtf_lb5)
        self.spin_mtf_lb5 = QSpinBox()
        self.spin_mtf_lb5.setRange(10, 500)
        self.spin_mtf_lb5.setSingleStep(20)
        self.spin_mtf_lb5.setValue(100)
        self.spin_mtf_lb5.valueChanged.connect(self._on_mtf_settings_changed)
        mtf_lb5_row.addWidget(self.spin_mtf_lb5)
        mv.addLayout(mtf_lb5_row)

        mtf_lb1_row = QHBoxLayout()
        mtf_lb1_row.setSpacing(8)
        lbl_mtf_lb1 = _lbl("🕯 1M Lookback:")
        lbl_mtf_lb1.setFixedWidth(100)
        mtf_lb1_row.addWidget(lbl_mtf_lb1)
        self.spin_mtf_lb1 = QSpinBox()
        self.spin_mtf_lb1.setRange(10, 1000)
        self.spin_mtf_lb1.setSingleStep(50)
        self.spin_mtf_lb1.setValue(200)
        self.spin_mtf_lb1.valueChanged.connect(self._on_mtf_settings_changed)
        mtf_lb1_row.addWidget(self.spin_mtf_lb1)
        mv.addLayout(mtf_lb1_row)

        mtf_max_row = QHBoxLayout()
        mtf_max_row.setSpacing(8)
        lbl_mtf_max = _lbl("🔲 Max Zones:")
        lbl_mtf_max.setFixedWidth(100)
        mtf_max_row.addWidget(lbl_mtf_max)
        self.spin_mtf_max = QSpinBox()
        self.spin_mtf_max.setRange(1, 50)
        self.spin_mtf_max.setSingleStep(5)
        self.spin_mtf_max.setValue(20)
        self.spin_mtf_max.valueChanged.connect(self._on_mtf_settings_changed)
        mtf_max_row.addWidget(self.spin_mtf_max)
        mv.addLayout(mtf_max_row)

        self.lbl_mtf_count = QLabel("MTF FVG: —")
        self.lbl_mtf_count.setStyleSheet(
            f"color:{C['gold']};font-family:Consolas;font-size:10px;")
        mv.addWidget(self.lbl_mtf_count)

        vl.addWidget(grp_mtf)

        # ── AMD Quarter Theory ────────────────────────────────────
        grp_amd = QGroupBox("🟩  AMD Quarter Theory")
        grp_amd.setStyleSheet(
            f"QGroupBox {{ background:{C['card']};border:1px solid {C['amd_a']};"
            f"border-radius:6px;margin-top:14px;padding:8px 6px 6px 6px;"
            f"font-size:10px;font-weight:bold;color:{C['amd_a']}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:10px;padding:0 4px; }}"
        )
        av = QVBoxLayout(grp_amd)
        av.setSpacing(6)

        self.chk_amd = QCheckBox("Enable AMD detection")
        self.chk_amd.setChecked(False)
        self.chk_amd.setToolTip(
            "Quarter Theory AMD phases on chart\n"
            "🟩 A = Accumulation  🟥 M = Manipulation  🟦 D = Distribution\n\n"
            "Boxes drawn for each level. Info table top-right of chart.\n"
            "Year → Quarter → Month → Week → Day → 4H → 1H → 5M → 1M"
        )
        self.chk_amd.stateChanged.connect(self._on_amd_toggled)
        av.addWidget(self.chk_amd)

        # Show all phases or current only
        self.chk_amd_all = QCheckBox("Show all phases (not just current)")
        self.chk_amd_all.setChecked(False)
        self.chk_amd_all.setToolTip(
            "When ON: draws all A/M/D/C boxes for the full period\n"
            "When OFF: draws only the currently active phase box per level"
        )
        self.chk_amd_all.stateChanged.connect(self._on_amd_settings_changed)
        av.addWidget(self.chk_amd_all)

        # Level checkboxes
        av.addWidget(_lbl("📊 Visible levels:"))
        self._amd_level_checks = {}
        level_colors = {
            "1M":      C['txt3'],
            "5M":      C['txt2'],
            "1H":      C['cyan'],
            "4H":      C['blue'],
            "Day":     C['gold'],
            "Week":    C['orange'],
            "Month":   C['amd_m'],
            "Quarter": C['purple'],
        }
        for level in ["1M", "5M", "1H", "4H", "Day", "Week", "Month", "Quarter"]:
            chk = QCheckBox(level)
            chk.setChecked(level in DEFAULT_LEVELS)
            chk.setStyleSheet(f"color:{level_colors.get(level, C['txt2'])};")
            chk.stateChanged.connect(self._on_amd_settings_changed)
            self._amd_level_checks[level] = chk
            av.addWidget(chk)

        # AMD status display
        self.lbl_amd_status = QLabel("AMD: —")
        self.lbl_amd_status.setStyleSheet(
            f"color:{C['amd_a']};font-family:Consolas;font-size:10px;")
        self.lbl_amd_status.setWordWrap(True)
        av.addWidget(self.lbl_amd_status)

        vl.addWidget(grp_amd)

        # ── ICT Bias Analyzer ──────────────────────────────────────
        grp_bias = QGroupBox("🧭  ICT Multi-Timeframe Bias (M1→H1)")
        grp_bias.setStyleSheet(
            f"QGroupBox {{ background:{C['card']};border:1px solid {C['cyan']};"
            f"border-radius:6px;margin-top:14px;padding:8px 6px 6px 6px;"
            f"font-size:10px;font-weight:bold;color:{C['cyan']}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:10px;padding:0 4px; }}"
        )
        bv = QVBoxLayout(grp_bias)
        bv.setSpacing(6)

        self.chk_bias = QCheckBox("Enable ICT Bias analysis")
        self.chk_bias.setChecked(False)
        self.chk_bias.setToolTip(
            "Scans M1 → H1 every tick using 6 ICT parameters:\n"
            "  • Market Structure (BOS / CHoCH)  — weight 3.0\n"
            "  • Premium / Discount arrays       — weight 2.0\n"
            "  • Fair Value Gaps (net imbalance) — weight 2.0\n"
            "  • Order Blocks (nearest above/below) — weight 2.5\n"
            "  • Previous Day High / Low         — weight 1.5\n"
            "  • EMA-20 Momentum slope           — weight 1.0\n\n"
            "Result shows bull % chance per timeframe + dominant\n"
            "direction (BULLISH / BEARISH / MIXED).\n"
            "Updates in log panel whenever bias shifts on any TF."
        )
        self.chk_bias.stateChanged.connect(self._on_bias_toggled)
        bv.addWidget(self.chk_bias)

        bias_lb_row = QHBoxLayout()
        bias_lb_row.setSpacing(8)
        lbl_bias_lb = _lbl("🕯 Lookback bars:")
        lbl_bias_lb.setFixedWidth(110)
        lbl_bias_lb.setToolTip(
            "How many candles to scan on each timeframe.\n"
            "Higher = more context (slower). Lower = more reactive."
        )
        bias_lb_row.addWidget(lbl_bias_lb)
        self.spin_bias_lookback = QSpinBox()
        self.spin_bias_lookback.setRange(20, 500)
        self.spin_bias_lookback.setSingleStep(10)
        self.spin_bias_lookback.setValue(100)
        self.spin_bias_lookback.setToolTip("Candles to analyze per timeframe")
        bias_lb_row.addWidget(self.spin_bias_lookback)
        bv.addLayout(bias_lb_row)

        bias_int_row = QHBoxLayout()
        bias_int_row.setSpacing(8)
        lbl_bias_int = _lbl("⏱ Scan interval (s):")
        lbl_bias_int.setFixedWidth(110)
        lbl_bias_int.setToolTip(
            "How often to re-analyze bias (seconds).\n"
            "10–30s is suitable for live trading."
        )
        bias_int_row.addWidget(lbl_bias_int)
        self.spin_bias_interval = QDoubleSpinBox()
        self.spin_bias_interval.setRange(5.0, 120.0)
        self.spin_bias_interval.setSingleStep(5.0)
        self.spin_bias_interval.setValue(10.0)
        self.spin_bias_interval.setDecimals(0)
        self.spin_bias_interval.setSuffix(" s")
        bias_int_row.addWidget(self.spin_bias_interval)
        bv.addLayout(bias_int_row)

        bias_hint = QLabel(
            "Output: 🧭 Bias | M1:🟢68%  M5:🔴61%  M15:⚪52%  M30:🟢71%  H1:🟢75%\n"
            "→ Net: 3🟢 1🔴 1⚪  | Dominant: BULLISH (Moderate)"
        )
        bias_hint.setStyleSheet(
            f"color:{C['txt3']};font-size:10px;font-style:italic;"
            f"background:{C['input']};border-radius:4px;padding:5px;"
        )
        bias_hint.setWordWrap(True)
        bv.addWidget(bias_hint)

        vl.addWidget(grp_bias)

        vl.addStretch()
        return outer

    # ── Report Tab ───────────────────────────────────────────────
