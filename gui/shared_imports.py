"""gui/shared_imports.py — All external imports used across GUI mixins.
Import with: from .shared_imports import *
"""

from core.rect_suggest_watcher import RectSuggestWatcher
from core.mtf_fvg_watcher import MTFFVGWatcher
from core.amd_watcher import AMDWatcher, ALL_LEVELS, DEFAULT_LEVELS
from core.confluence_watcher import ConfluenceWatcher
from core.ob_watcher import OBWatcher
from core.fvg_watcher import FVGWatcher
from core.position_monitor import SourceState
from core.watcher import WatcherThread
from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    WATCH_SYMBOL, SCAN_INTERVAL_SEC,
    LOT_SIZE, MAGIC_NUMBER,
)
import config as cfg
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QLinearGradient, QPen
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QTextEdit, QFrame,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QDoubleSpinBox, QSpinBox, QComboBox, QSplitter, QSizePolicy,
    QProgressBar, QCheckBox, QScrollArea, QLineEdit,
    QSystemTrayIcon, QMenu, QAction,
)
import MetaTrader5 as mt5
import sys
import os
from core.profile import load_profile, save_profile, inject_into_config, profile_exists
from setup_dialog import SetupDialog
from core.updater import UpdateChecker, UpdateDownloader, launch_installer_and_exit, APP_VERSION
from core.bias_watcher import BiasWatcher
from core.notifications import manager as notif_manager
from core.trade_db import db as trade_db
from account_dialog import AccountDialog
import threading
from datetime import datetime
from typing import Optional

__all__ = [
    "RectSuggestWatcher",
    "MTFFVGWatcher",
    "AMDWatcher",
    "ALL_LEVELS",
    "DEFAULT_LEVELS",
    "ConfluenceWatcher",
    "OBWatcher",
    "FVGWatcher",
    "SourceState",
    "WatcherThread",
    "MT5_LOGIN",
    "MT5_PASSWORD",
    "MT5_SERVER",
    "WATCH_SYMBOL",
    "SCAN_INTERVAL_SEC",
    "LOT_SIZE",
    "MAGIC_NUMBER",
    "cfg",
    "QColor",
    "QFont",
    "QPainter",
    "QPainterPath",
    "QLinearGradient",
    "QPen",
    "Qt",
    "QTimer",
    "pyqtSignal",
    "QObject",
    "QApplication",
    "QMainWindow",
    "QWidget",
    "QVBoxLayout",
    "QHBoxLayout",
    "QLabel",
    "QPushButton",
    "QGroupBox",
    "QTextEdit",
    "QFrame",
    "QTabWidget",
    "QTableWidget",
    "QTableWidgetItem",
    "QHeaderView",
    "QDoubleSpinBox",
    "QSpinBox",
    "QComboBox",
    "QSplitter",
    "QSizePolicy",
    "QProgressBar",
    "QCheckBox",
    "QScrollArea",
    "QLineEdit",
    "QSystemTrayIcon",
    "QMenu",
    "QAction",
    "mt5",
    "sys",
    "os",
    "load_profile",
    "save_profile",
    "inject_into_config",
    "profile_exists",
    "SetupDialog",
    "UpdateChecker",
    "UpdateDownloader",
    "launch_installer_and_exit",
    "APP_VERSION",
    "BiasWatcher",
    "notif_manager",
    "trade_db",
    "AccountDialog",
    "threading",
    "datetime",
    "Optional",
]
