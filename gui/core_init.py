"""
gui/core_init.py — GUI __init__, tray, auto-update, profile, session wiring
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
from core.license import validate_license, LicenseStatus
from license_dialog import LicenseDialog
try:
    from server import start_server as _start_api_server
    _HAS_SERVER = True
except ImportError:
    _HAS_SERVER = False


class CoreInitMixin:
    def __init__(self):
        super().__init__()

        # ── License check — must pass before app opens ────────────
        self._check_license()

        # ── Start remote control server ─────────────────────────
        if _HAS_SERVER:
            try:
                _start_api_server(host="0.0.0.0", port=8000)
            except Exception as _e:
                import logging
                logging.getLogger("gui").debug(
                    "API server failed to start: %s", _e)

        self.setWindowTitle("TraderBot v4 — Rectangle-Anchored Recovery Bot")
        self.setMinimumSize(900, 660)
        self.setStyleSheet(SS)

        self._worker:            Optional[WatcherThread] = None
        self._fvg_worker:        Optional[FVGWatcher] = None
        self._ob_worker:         Optional[OBWatcher] = None
        self._bias_worker:       Optional[BiasWatcher] = None
        self._confluence_worker: Optional[ConfluenceWatcher] = None
        self._amd_worker:        Optional[AMDWatcher] = None
        self._mtf_fvg_worker:    Optional[MTFFVGWatcher] = None
        self._rect_suggest_worker: Optional[RectSuggestWatcher] = None

        self._sig = Sig()
        self._sig.log_line.connect(self._on_log)
        self._sig.status.connect(self._on_status)
        self._sig.state.connect(self._on_state)
        self._sig.candle.connect(self._on_candle)
        # ← NEW: balance TP fires on Qt main thread — safe to stop all watchers
        self._sig.balance_tp.connect(self._on_balance_tp_reached)
        self._sig.bias_update.connect(self._on_bias_update)

        self._last_candle: dict = {}

        self._build_ui()

        # ── System tray icon ──────────────────────────────────────
        self._tray = None
        self._init_tray()

        # Load user profile (credentials + preferences) on startup.
        # If no profile exists (first run), show the setup wizard.
        self._profile = load_profile()
        if not profile_exists():
            QTimer.singleShot(200, self._show_first_run_setup)
        else:
            inject_into_config(self._profile)
            self._apply_profile_to_ui(self._profile)

        # Background update check — silent, non-blocking
        self._update_info = None
        self._update_bar = None   # injected into UI when update found
        QTimer.singleShot(3000, self._start_update_check)

        # Price ticker
        self._pt = QTimer()
        self._pt.timeout.connect(self._refresh_price)
        self._pt.start(1000)

        # Orders auto-refresh
        self._ot = QTimer()
        self._ot.timeout.connect(self._refresh_orders)
        self._ot.start(3000)

        # Indicator count refresh
        self._ct = QTimer()
        self._ct.timeout.connect(self._refresh_indicator_counts)
        self._ct.start(5000)

        QTimer.singleShot(200, self._init_price)

        # AMD status refresh
        self._at = QTimer()
        self._at.timeout.connect(self._refresh_amd_status)
        self._at.start(10000)

    # ── License ──────────────────────────────────────────────────

    def _check_license(self):
        import sys
        status, info = validate_license()
        reason_map = {
            LicenseStatus.NOT_FOUND:     "No license found on this machine.",
            LicenseStatus.INVALID:       "License key is invalid or has been tampered with.",
            LicenseStatus.EXPIRED:       "Your license has expired. Contact the developer to renew.",
            LicenseStatus.WRONG_DEVICE:  "This license is registered to a different machine.",
        }
        if status == LicenseStatus.OK:
            if info:
                user = info.get("user", "")
                expiry = info.get("expiry", "never")
                if user:
                    exp_str = f" ({expiry})" if expiry != "never" else ""
                    self.setWindowTitle(
                        f"TraderBot v4 — {user}{exp_str}")
            return
        reason = reason_map.get(status, "License validation failed.")
        dlg = LicenseDialog(reason=reason)
        dlg.exec_()
        if not dlg.is_activated():
            sys.exit(0)
        status2, _ = validate_license()
        if status2 != LicenseStatus.OK:
            sys.exit(0)

    # ── Tunnel URL watcher ───────────────────────────────────────

    def _start_tunnel_url_watcher(self):
        """Watch for Cloudflare tunnel URL and show it in the GUI log."""
        def _watch():
            try:
                from core.tunnel import tunnel as _t
                url = _t.wait_for_url(timeout=20.0)
                if url:
                    self._sig.log_line.emit(
                        f"🌐  Remote Access ready\n"
                        f"   URL: {url}\n"
                        f"   Key: see %APPDATA%\\TraderBotV4\\api_key.txt",
                        "NEW"
                    )
            except Exception:
                pass
        import threading
        threading.Thread(target=_watch, daemon=True).start()

    # ── UI Build ──────────────────────────────────────────────────

    def _init_tray(self):
        try:
            from PyQt5.QtGui import QIcon
            self._tray = QSystemTrayIcon(self)
            self._tray.setIcon(self.style().standardIcon(
                self.style().SP_ComputerIcon))
            self._tray.setToolTip("TraderBot v4")
            tray_menu = QMenu()
            act_show = QAction("Show", self)
            act_show.triggered.connect(self.show)
            tray_menu.addAction(act_show)
            act_quit = QAction("Quit", self)
            act_quit.triggered.connect(self.close)
            tray_menu.addAction(act_quit)
            self._tray.setContextMenu(tray_menu)
            self._tray.activated.connect(
                lambda r: self.show() if r == QSystemTrayIcon.DoubleClick else None)
            self._tray.show()
            notif_manager.tray_icon = self._tray
        except Exception as e:
            import logging
            logging.getLogger("gui").debug("Tray init failed: %s", e)

    # ── Trade event recorder ──────────────────────────────────────

    def _record_trade_event(self, msg: str, level: str):
        """
        Parse log messages and record closed trades to SQLite.
        Detects TP wins, SL losses, risk-free/loss-free closes.
        """
        import re
        sym = self.sym_combo.currentText().strip() if hasattr(self, "sym_combo") else ""

        # TP win: "🏆  [name] BUY hit TP — round won"
        m = re.search(
            r"🏆.*\[(.*?)\]\s+(BUY|SELL)\s+hit TP", msg)
        if m:
            trade_db.record_trade(
                symbol=sym, ticket=0, side=m.group(2).lower(),
                lot=0, entry_price=0, exit_price=0,
                result="tp", rect_name=m.group(1), notes=msg[:200])
            return

        # SL loss: "📉  [name] BUY pos#TICKET closed @ PRICE (sl)"
        m = re.search(
            r"📉.*\[(.*?)\]\s+(BUY|SELL)\s+pos#(\d+)\s+closed\s*@\s*([\d.]+)\s*\(sl\)",
            msg)
        if m:
            trade_db.record_trade(
                symbol=sym, ticket=int(m.group(3)),
                side=m.group(2).lower(), lot=0,
                entry_price=0, exit_price=float(m.group(4)),
                result="sl", rect_name=m.group(1), notes=msg[:200])
            return

        # Risk-free / loss-free closed
        m = re.search(
            r"(risk.free|loss.free)\s+(BUY|SELL)\s+closed", msg, re.I)
        if m:
            result = "risk_free" if "risk" in m.group(
                1).lower() else "loss_free"
            trade_db.record_trade(
                symbol=sym, ticket=0, side=m.group(2).lower(),
                lot=0, entry_price=0, exit_price=0,
                result=result, notes=msg[:200])
            return

        # Balance TP
        if re.search(r"balance tp|🎯.*balance", msg, re.I):
            trade_db.record_trade(
                symbol=sym, ticket=0, side="",
                lot=0, entry_price=0, exit_price=0,
                result="balance_tp", notes=msg[:200])

    def _start_update_check(self):
        checker = UpdateChecker(
            on_update_available=self._on_update_available,
        )
        checker.start()

    def _on_update_available(self, info: dict):
        """Called from background thread — must marshal to Qt main thread."""
        # Use a queued signal-safe approach via QTimer.singleShot
        self._update_info = info
        QTimer.singleShot(0, self._show_update_banner)

    def _show_update_banner(self):
        info = self._update_info
        if not info:
            return
        ver = info.get("version", "?")
        notes = info.get("release_notes", "")

        # Inject a slim update bar at the top of the left sidebar
        # (find the first widget in the scroll area's layout)
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background:#1a2a1a; border:1px solid {C['green']}; "
            f"border-radius:5px; margin:4px; }}"
        )
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 6, 10, 6)

        lbl = QLabel(f"🆕  v{ver} available" + (f" — {notes}" if notes else ""))
        lbl.setStyleSheet(
            f"color:{C['green']};font-size:12px;font-weight:bold;border:none;")
        bl.addWidget(lbl, 1)

        btn = QPushButton("Update Now")
        btn.setFixedWidth(100)
        btn.setFixedHeight(26)
        btn.setStyleSheet(
            f"background:{C['green_dk']};color:{C['green']};"
            f"border:1px solid {C['green']};border-radius:4px;font-size:11px;"
        )
        btn.clicked.connect(self._start_download_update)
        bl.addWidget(btn)

        dismiss = QPushButton("✕")
        dismiss.setFixedWidth(24)
        dismiss.setFixedHeight(24)
        dismiss.setStyleSheet(
            f"background:transparent;color:{C['txt3']};border:none;font-size:11px;"
        )
        dismiss.clicked.connect(lambda: bar.setVisible(False))
        bl.addWidget(dismiss)

        self._update_bar = bar
        # Insert at top of the main left-panel layout
        self._left_panel_layout.insertWidget(0, bar)

    def _start_download_update(self):
        info = self._update_info
        if not info:
            return
        url = info.get("download_url", "")
        if not url:
            self._on_log(
                f"{datetime.now().strftime('%H:%M:%S')}  "
                "⚠️  Update URL missing from version manifest.", "WARN"
            )
            return

        # Replace the update bar with a progress display
        if self._update_bar:
            self._update_bar.setVisible(False)

        self._dl_bar = QFrame()
        self._dl_bar.setStyleSheet(
            f"QFrame {{ background:#1a1a2a; border:1px solid {C['cyan']}; "
            f"border-radius:5px; margin:4px; }}"
        )
        dl_l = QVBoxLayout(self._dl_bar)
        dl_l.setContentsMargins(10, 6, 10, 6)
        dl_l.setSpacing(4)

        self._dl_lbl = QLabel("⬇  Downloading update…")
        self._dl_lbl.setStyleSheet(
            f"color:{C['cyan']};font-size:12px;border:none;")
        dl_l.addWidget(self._dl_lbl)

        self._dl_prog = QProgressBar()
        self._dl_prog.setRange(0, 100)
        self._dl_prog.setValue(0)
        self._dl_prog.setFixedHeight(6)
        self._dl_prog.setTextVisible(False)
        self._dl_prog.setStyleSheet(
            f"QProgressBar {{ background:{C['input']}; border:1px solid {C['border']}; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{C['cyan']}; border-radius:3px; }}"
        )
        dl_l.addWidget(self._dl_prog)

        self._left_panel_layout.insertWidget(0, self._dl_bar)

        self._downloader = UpdateDownloader(
            url=url,
            on_progress=self._on_dl_progress,
            on_done=self._on_dl_done,
            on_error=self._on_dl_error,
        )
        self._downloader.start()

    def _on_dl_progress(self, done: int, total: int):
        QTimer.singleShot(0, lambda: self._apply_dl_progress(done, total))

    def _apply_dl_progress(self, done: int, total: int):
        if total > 0:
            pct = int(done * 100 / total)
            self._dl_prog.setValue(pct)
            mb_done = done / (1024*1024)
            mb_total = total / (1024*1024)
            self._dl_lbl.setText(f"⬇  {mb_done:.1f} / {mb_total:.1f} MB")
        else:
            mb = done / (1024*1024)
            self._dl_lbl.setText(f"⬇  {mb:.1f} MB downloaded…")

    def _on_dl_done(self, path: str):
        QTimer.singleShot(0, lambda: self._apply_dl_done(path))

    def _apply_dl_done(self, path: str):
        self._dl_lbl.setText("✅  Download complete — launching installer…")
        self._dl_prog.setValue(100)
        self._on_log(
            f"{datetime.now().strftime('%H:%M:%S')}  "
            f"✅  Update downloaded. Launching installer and closing bot.", "NEW"
        )
        # Stop the bot cleanly before handing over to the installer
        QTimer.singleShot(1200, lambda: self._launch_update(path))

    def _launch_update(self, path: str):
        self._stop()
        launch_installer_and_exit(path)

    def _on_dl_error(self, msg: str):
        QTimer.singleShot(0, lambda: self._apply_dl_error(msg))

    def _apply_dl_error(self, msg: str):
        if hasattr(self, "_dl_lbl"):
            self._dl_lbl.setText(f"❌  Download failed: {msg}")
        self._on_log(
            f"{datetime.now().strftime('%H:%M:%S')}  "
            f"❌  Update download failed: {msg}", "ERROR"
        )

    # ── Profile / Settings ───────────────────────────────────────

    def _show_first_run_setup(self):
        """Show the first-run setup wizard. Blocks until complete."""
        dlg = SetupDialog(parent=self, title="First-Time Setup")
        dlg.profile_saved.connect(self._on_profile_saved)
        result = dlg.exec_()
        if result != SetupDialog.Accepted or not dlg.profile:
            # User closed without completing — show a warning but
            # don't block the app; they can open Settings manually.
            self._on_log(
                f"{datetime.now().strftime('%H:%M:%S')}  "
                "⚠️  Setup not completed — open ⚙ Account & Settings to enter your MT5 credentials.",
                "WARN"
            )
        else:
            self._profile = dlg.profile
            inject_into_config(self._profile)
            self._apply_profile_to_ui(self._profile)

    def _show_settings(self):
        """Open the Account & Settings panel."""
        dlg = AccountDialog(
            parent=self,
            profile=self._profile,
            notif_settings=notif_manager,
        )
        dlg.profile_saved.connect(self._on_profile_saved)
        dlg.exec_()

    def _on_profile_saved(self, profile: dict):
        """Called when SetupDialog saves a profile."""
        self._profile = profile
        inject_into_config(profile)
        self._apply_profile_to_ui(profile)
        self._on_log(
            f"{datetime.now().strftime('%H:%M:%S')}  "
            "✅  Account settings saved.", "NEW"
        )

    def _apply_profile_to_ui(self, profile: dict):
        """Sync saved profile values into GUI controls."""
        # Symbol
        sym = profile.get("watch_symbol", "")
        if sym:
            idx = self.sym_combo.findText(sym)
            if idx >= 0:
                self.sym_combo.setCurrentIndex(idx)
            else:
                # Symbol not in list yet — add it
                self.sym_combo.insertItem(0, sym)
                self.sym_combo.setCurrentIndex(0)

        # Base lot
        lot = float(profile.get("lot_size", 0.01))
        self.spin_lot.blockSignals(True)
        self.spin_lot.setValue(lot)
        self.spin_lot.blockSignals(False)
        self._on_base_lot_changed(lot)

        # Lot mode
        mode = int(profile.get("soft_lot_mode", 1))
        self.lot_mode_combo.setCurrentIndex({1: 0, 2: 1, 3: 2}.get(mode, 0))

        # Window title
        name = profile.get("display_name", "")
        server = profile.get("mt5_server", "")
        if name or server:
            suffix = name or server
            self.setWindowTitle(
                f"TraderBot v4 — {suffix}")
