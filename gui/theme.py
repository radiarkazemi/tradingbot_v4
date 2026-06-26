"""gui/theme.py — Color palette, global stylesheet, shared UI helpers."""
"""
╔══════════════════════════════════════════════════════════════════╗
║  TraderBot v4 — GUI                                              ║
║  Rectangle-Anchored 2-Leg Recovery Bot                           ║
║  python gui.py                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
from typing import Optional
from datetime import datetime
import threading
from account_dialog import AccountDialog
from core.trade_db import db as trade_db
from core.notifications import manager as notif_manager
from core.bias_watcher import BiasWatcher
from core.updater import UpdateChecker, UpdateDownloader, launch_installer_and_exit, APP_VERSION
from setup_dialog import SetupDialog
from core.profile import load_profile, save_profile, inject_into_config, profile_exists
import os
import sys
import MetaTrader5 as mt5
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QTextEdit, QFrame,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QDoubleSpinBox, QSpinBox, QComboBox, QSplitter, QSizePolicy,
    QProgressBar, QCheckBox, QScrollArea, QLineEdit,
    QSystemTrayIcon, QMenu, QAction,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QLinearGradient, QPen
import config as cfg
from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    WATCH_SYMBOL, SCAN_INTERVAL_SEC,
    LOT_SIZE, MAGIC_NUMBER,
)
from core.watcher import WatcherThread
from core.position_monitor import SourceState
from core.fvg_watcher import FVGWatcher
from core.ob_watcher import OBWatcher
from core.confluence_watcher import ConfluenceWatcher
from core.amd_watcher import AMDWatcher, ALL_LEVELS, DEFAULT_LEVELS
from core.rect_suggest_watcher import RectSuggestWatcher
from core.mtf_fvg_watcher import MTFFVGWatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


os.makedirs("logs", exist_ok=True)


# ── Palette ───────────────────────────────────────────────────────
C = {
    "bg":       "#0D1117",
    "panel":    "#161B22",
    "card":     "#1C2333",
    "input":    "#141D2E",
    "border":   "#2A3550",
    "border_hi": "#4A6090",
    "txt":      "#E8EDF5",
    "txt2":     "#8B9BB4",
    "txt3":     "#4A5568",
    "gold":     "#F5A623",
    "green":    "#00D97E",
    "green_dk": "#003D22",
    "red":      "#FF4560",
    "red_dk":   "#3D0015",
    "orange":   "#FF8C00",
    "cyan":     "#00BCD4",
    "blue":     "#2979FF",
    "purple":   "#B388FF",
    "aqua":     "#00FFFF",
    "magenta":  "#FF00FF",
    "yellow":   "#FFD700",
    "amd_a":    "#00C853",
    "amd_m":    "#FF1744",
    "amd_d":    "#2979FF",
}

SS = f"""
QWidget      {{ background:{C['bg']};color:{C['txt']};font-family:'Segoe UI'; }}
QMainWindow  {{ background:{C['bg']}; }}
QLabel       {{ background:transparent;font-size:12px; }}
QGroupBox    {{ background:{C['card']};border:1px solid {C['border']};border-radius:6px;
                margin-top:14px;padding:8px 6px 6px 6px;
                font-size:10px;font-weight:bold;color:{C['txt2']}; }}
QGroupBox::title {{ subcontrol-origin:margin;left:10px;padding:0 4px; }}
QPushButton  {{ background:{C['card']};color:{C['txt']};border:1px solid {C['border']};
                border-radius:5px;padding:6px 14px;font-size:12px; }}
QPushButton:hover   {{ background:{C['border']};border-color:{C['border_hi']}; }}
QPushButton:pressed {{ background:{C['bg']}; }}
QPushButton:disabled{{ color:{C['txt3']};border-color:{C['card']}; }}
QPushButton#btn_start {{ background:{C['green_dk']};color:{C['green']};
    border:1px solid {C['green']};font-weight:bold;font-size:13px; }}
QPushButton#btn_start:hover {{ background:{C['green']};color:#000; }}
QPushButton#btn_stop {{ background:{C['red_dk']};color:{C['red']};
    border:1px solid {C['red']};font-weight:bold;font-size:13px; }}
QPushButton#btn_stop:hover {{ background:{C['red']};color:#fff; }}
QPushButton#btn_cancel {{ background:{C['red_dk']};color:{C['red']};border:1px solid {C['red']}; }}
QPushButton#btn_cancel:hover {{ background:{C['red']};color:#fff; }}
QDoubleSpinBox,QSpinBox,QComboBox,QLineEdit {{
    background:{C['input']};color:{C['txt']};
    border:1px solid {C['border']};border-radius:4px;
    padding:4px 7px;min-height:26px;font-size:12px; }}
QDoubleSpinBox::up-button,QDoubleSpinBox::down-button,
QSpinBox::up-button,QSpinBox::down-button {{ background:{C['border']};border:none;width:16px; }}
QComboBox::drop-down {{ border:none;width:20px; }}
QComboBox QAbstractItemView {{ background:{C['card']};color:{C['txt']};
    selection-background-color:{C['border']}; }}
QTextEdit {{ background:{C['bg']};color:{C['txt']};border:1px solid {C['border']};
             border-radius:4px;font-family:'Consolas';font-size:11px; }}
QTableWidget {{ background:{C['bg']};color:{C['txt']};border:1px solid {C['border']};
                border-radius:4px;gridline-color:{C['border']};
                alternate-background-color:{C['panel']};font-size:11px; }}
QTableWidget::item {{ padding:4px 8px; }}
QTableWidget::item:selected {{ background:{C['border']}; }}
QHeaderView::section {{ background:{C['card']};color:{C['txt2']};padding:5px 8px;
    border:none;border-right:1px solid {C['border']};
    border-bottom:1px solid {C['border']};font-size:10px;font-weight:bold; }}
QTabWidget::pane {{ background:{C['panel']};border:1px solid {C['border']};border-radius:4px; }}
QTabBar::tab {{ background:{C['card']};color:{C['txt2']};padding:6px 18px;
    border:1px solid {C['border']};border-bottom:none;
    border-radius:4px 4px 0 0;margin-right:2px;font-size:11px; }}
QTabBar::tab:selected {{ background:{C['panel']};color:{C['gold']};border-bottom:2px solid {C['gold']}; }}
QTabBar::tab:hover:!selected {{ color:{C['txt']}; }}
QScrollArea {{ border:none;background:transparent; }}
QScrollBar:vertical {{ background:{C['bg']};width:6px; }}
QScrollBar::handle:vertical {{ background:{C['border']};border-radius:3px;min-height:20px; }}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
QCheckBox {{ color:{C['txt2']};font-size:12px; }}
QCheckBox::indicator {{ width:14px;height:14px;border:1px solid {C['border']};
    border-radius:3px;background:{C['input']}; }}
QCheckBox::indicator:checked {{ background:{C['cyan']};border-color:{C['cyan']}; }}
"""


# ── Qt Signal Bridge ──────────────────────────────────────────────
