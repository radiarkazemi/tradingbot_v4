"""
account_dialog.py — Account Panel for TraderBot v4

Separate from setup_dialog (first-run wizard). This is the full
account management panel accessible from the main app at any time.
Fully responsive layout. Tabs:
  1. MT5 Account     — credentials, server, display name
  2. Preferences     — default symbol, lot, mode
  3. Notifications   — per-event enable + sound toggles
  4. About           — version, update info
"""

import sys
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QDoubleSpinBox, QComboBox, QWidget, QTabWidget,
    QFrame, QCheckBox, QScrollArea, QSpinBox, QSizePolicy,
    QGroupBox, QFormLayout, QGridLayout,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

C = {
    "bg":        "#0D1117",
    "panel":     "#161B22",
    "card":      "#1C2333",
    "input":     "#141D2E",
    "border":    "#2A3550",
    "border_hi": "#4A6090",
    "txt":       "#E8EDF5",
    "txt2":      "#8B9BB4",
    "txt3":      "#4A5568",
    "gold":      "#F5A623",
    "green":     "#00D97E",
    "green_dk":  "#003D22",
    "red":       "#FF4560",
    "cyan":      "#00BCD4",
    "blue":      "#2979FF",
    "orange":    "#FF8C00",
}

SS = f"""
QDialog, QWidget {{ background:{C['bg']}; color:{C['txt']};
    font-family:'Segoe UI'; font-size:13px; }}
QLabel {{ background:transparent; }}
QTabWidget::pane {{ background:{C['panel']}; border:1px solid {C['border']};
    border-radius:4px; }}
QTabBar::tab {{ background:{C['card']}; color:{C['txt2']};
    padding:7px 16px; border:1px solid {C['border']};
    border-bottom:none; border-radius:4px 4px 0 0; font-size:12px; }}
QTabBar::tab:selected {{ background:{C['panel']}; color:{C['txt']};
    font-weight:bold; }}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
    background:{C['input']}; color:{C['txt']};
    border:1px solid {C['border']}; border-radius:5px;
    padding:7px 10px; min-height:28px; font-size:13px; }}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{
    border-color:{C['cyan']}; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button {{
    background:{C['border']}; border:none; width:18px; }}
QComboBox::drop-down {{ border:none; width:22px; }}
QComboBox QAbstractItemView {{ background:{C['card']}; color:{C['txt']};
    selection-background-color:{C['border']}; }}
QPushButton {{ background:{C['card']}; color:{C['txt']};
    border:1px solid {C['border']}; border-radius:6px;
    padding:8px 20px; font-size:13px; }}
QPushButton:hover {{ background:{C['border']}; border-color:{C['border_hi']}; }}
QPushButton#btn_save {{ background:{C['green_dk']}; color:{C['green']};
    border:1px solid {C['green']}; font-weight:bold; font-size:14px;
    padding:10px 30px; }}
QPushButton#btn_save:hover {{ background:{C['green']}; color:#000; }}
QCheckBox {{ color:{C['txt2']}; spacing:8px; }}
QCheckBox::indicator {{ width:16px; height:16px;
    border:1px solid {C['border']}; border-radius:3px;
    background:{C['input']}; }}
QCheckBox::indicator:checked {{ background:{C['cyan']};
    border-color:{C['cyan']}; }}
QGroupBox {{ border:1px solid {C['border']}; border-radius:6px;
    margin-top:14px; padding:10px 8px 8px 8px;
    font-size:11px; font-weight:bold; color:{C['txt2']}; }}
QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; }}
QScrollArea {{ border:none; }}
QFrame#divider {{ background:{C['border']}; max-height:1px; }}
"""


def _divider():
    f = QFrame()
    f.setObjectName("divider")
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{C['border']};border:none;")
    return f


def _lbl(text, color=None, size=13, bold=False):
    l = QLabel(text)
    s = f"font-size:{size}px;"
    if color:
        s += f"color:{color};"
    if bold:
        s += "font-weight:bold;"
    l.setStyleSheet(s)
    return l


def _section(title):
    l = QLabel(title)
    l.setStyleSheet(
        f"color:{C['gold']};font-size:11px;font-weight:bold;"
        f"letter-spacing:1px;text-transform:uppercase;"
        f"padding:6px 0 2px 0;background:transparent;")
    return l


class AccountDialog(QDialog):

    profile_saved = pyqtSignal(dict)

    def __init__(self, parent=None, profile: dict = None,
                 notif_settings=None):
        super().__init__(parent)
        self.setWindowTitle("TraderBot v4 — Account & Settings")
        self.setMinimumSize(540, 580)
        self.resize(600, 640)
        self.setModal(True)
        self.setStyleSheet(SS)
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)

        self._profile = dict(profile) if profile else {}
        self._notif = notif_settings

        self._build_ui()
        self._load_profile()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(52)
        hdr.setStyleSheet(
            f"background:{C['panel']};border-bottom:1px solid {C['border']};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(18, 0, 18, 0)
        hl.addWidget(_lbl("⚙  Account & Settings",
                          C['gold'], size=15, bold=True))
        hl.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(
            f"background:transparent;color:{C['txt3']};"
            f"border:none;font-size:14px;")
        close_btn.clicked.connect(self.reject)
        hl.addWidget(close_btn)
        root.addWidget(hdr)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._tab_account(),       "👤  MT5 Account")
        self._tabs.addTab(self._tab_preferences(),   "⚙️  Preferences")
        self._tabs.addTab(self._tab_notifications(), "🔔  Notifications")
        self._tabs.addTab(self._tab_about(),         "ℹ️   About")
        root.addWidget(self._tabs, 1)

        # Footer
        ftr = QWidget()
        ftr.setFixedHeight(60)
        ftr.setStyleSheet(
            f"background:{C['panel']};border-top:1px solid {C['border']};")
        fl = QHBoxLayout(ftr)
        fl.setContentsMargins(18, 0, 18, 0)
        fl.setSpacing(12)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color:{C['green']};font-size:12px;")
        fl.addWidget(self._status_lbl)
        fl.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(90)
        cancel_btn.clicked.connect(self.reject)
        fl.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("btn_save")
        save_btn.setFixedWidth(110)
        save_btn.clicked.connect(self._save)
        fl.addWidget(save_btn)
        root.addWidget(ftr)

    # ── Tab 1: MT5 Account ────────────────────────────────────────

    def _tab_account(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vl = QVBoxLayout(inner)
        vl.setSpacing(4)
        vl.setContentsMargins(18, 14, 18, 14)

        vl.addWidget(_section("Display"))
        vl.addWidget(_lbl("Display Name", C['txt2'], 11))
        self.inp_name = QLineEdit()
        self.inp_name.setPlaceholderText("e.g. My Gold Account")
        vl.addWidget(self.inp_name)
        vl.addSpacing(6)

        vl.addWidget(_divider())
        vl.addSpacing(6)
        vl.addWidget(_section("MT5 Credentials"))

        vl.addWidget(_lbl("Account Login (number)", C['txt2'], 11))
        self.inp_login = QLineEdit()
        self.inp_login.setPlaceholderText("e.g. 12345678")
        vl.addWidget(self.inp_login)
        vl.addSpacing(4)

        vl.addWidget(_lbl("Password", C['txt2'], 11))
        pw_row = QHBoxLayout()
        self.inp_pw = QLineEdit()
        self.inp_pw.setEchoMode(QLineEdit.Password)
        self.inp_pw.setPlaceholderText("MT5 account password")
        pw_row.addWidget(self.inp_pw)
        show_pw = QCheckBox("Show")
        show_pw.stateChanged.connect(
            lambda s: self.inp_pw.setEchoMode(
                QLineEdit.Normal if s else QLineEdit.Password))
        pw_row.addWidget(show_pw)
        vl.addLayout(pw_row)
        vl.addSpacing(4)

        vl.addWidget(_lbl("Server Address", C['txt2'], 11))
        self.inp_server = QLineEdit()
        self.inp_server.setPlaceholderText("e.g. LiteFinance-MT5-Demo")
        vl.addWidget(self.inp_server)
        vl.addSpacing(4)

        self.err_lbl = QLabel("")
        self.err_lbl.setStyleSheet(f"color:{C['red']};font-size:12px;")
        self.err_lbl.setWordWrap(True)
        vl.addWidget(self.err_lbl)
        vl.addStretch()
        scroll.setWidget(inner)
        return scroll

    # ── Tab 2: Preferences ────────────────────────────────────────

    def _tab_preferences(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vl = QVBoxLayout(inner)
        vl.setSpacing(4)
        vl.setContentsMargins(18, 14, 18, 14)

        vl.addWidget(_section("Trading Defaults"))
        vl.addWidget(_lbl("Default Symbol", C['txt2'], 11))
        self.inp_symbol = QLineEdit()
        self.inp_symbol.setPlaceholderText("e.g. XAUUSD_o or EURUSD")
        vl.addWidget(self.inp_symbol)
        vl.addSpacing(4)

        row = QHBoxLayout()
        col1 = QVBoxLayout()
        col1.addWidget(_lbl("Base Lot Size", C['txt2'], 11))
        self.spin_lot = QDoubleSpinBox()
        self.spin_lot.setRange(0.01, 100.0)
        self.spin_lot.setSingleStep(0.01)
        self.spin_lot.setDecimals(2)
        self.spin_lot.setValue(0.01)
        col1.addWidget(self.spin_lot)
        row.addLayout(col1)
        row.addSpacing(12)
        col2 = QVBoxLayout()
        col2.addWidget(_lbl("Lot Mode", C['txt2'], 11))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems([
            "Mode 1  (+0.01/touch, max 0.11)",
            "Mode 2  (+0.02/touch, max 0.20)",
            "Mode 3  (Classic Martingale 2×)",
        ])
        col2.addWidget(self.combo_mode)
        row.addLayout(col2)
        vl.addLayout(row)
        vl.addSpacing(10)

        vl.addWidget(_divider())
        vl.addSpacing(6)
        vl.addWidget(_section("Risk Management"))

        row2 = QHBoxLayout()
        col3 = QVBoxLayout()
        col3.addWidget(_lbl("Balance TP target (%)", C['txt2'], 11))
        self.spin_bal_tp = QDoubleSpinBox()
        self.spin_bal_tp.setRange(1.0, 100.0)
        self.spin_bal_tp.setSingleStep(1.0)
        self.spin_bal_tp.setDecimals(1)
        self.spin_bal_tp.setValue(10.0)
        self.spin_bal_tp.setSuffix(" %")
        col3.addWidget(self.spin_bal_tp)
        row2.addLayout(col3)
        row2.addSpacing(12)
        col4 = QVBoxLayout()
        col4.addWidget(_lbl("Max Touches (0 = unlimited)", C['txt2'], 11))
        self.spin_max_touch = QSpinBox()
        self.spin_max_touch.setRange(0, 99)
        self.spin_max_touch.setValue(12)
        col4.addWidget(self.spin_max_touch)
        row2.addLayout(col4)
        vl.addLayout(row2)
        vl.addStretch()
        scroll.setWidget(inner)
        return scroll

    # ── Tab 3: Notifications ──────────────────────────────────────

    def _tab_notifications(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vl = QVBoxLayout(inner)
        vl.setSpacing(6)
        vl.setContentsMargins(18, 14, 18, 14)

        # Global switches
        global_grp = QGroupBox("Global")
        gl = QVBoxLayout(global_grp)
        self.chk_notif_enabled = QCheckBox(
            "Enable desktop notifications (Windows tray)")
        self.chk_notif_enabled.setChecked(True)
        gl.addWidget(self.chk_notif_enabled)
        self.chk_sound_enabled = QCheckBox("Enable sounds (Windows beep)")
        self.chk_sound_enabled.setChecked(True)
        gl.addWidget(self.chk_sound_enabled)
        vl.addWidget(global_grp)

        # Per-event table
        vl.addWidget(_section("Per-Event Settings"))
        hint = _lbl(
            "Notify = show desktop popup   Sound = play audio alert",
            C['txt3'], 11)
        hint.setWordWrap(True)
        vl.addWidget(hint)

        from core.notifications import EVENT_LABELS
        events_grp = QGroupBox("Events")
        ev_l = QGridLayout(events_grp)
        ev_l.setColumnStretch(0, 1)

        ev_l.addWidget(_lbl("Event", C['txt3'], 11), 0, 0)
        ev_l.addWidget(_lbl("Notify", C['txt3'], 11, True), 0, 1,
                       Qt.AlignCenter)
        ev_l.addWidget(_lbl("Sound", C['txt3'], 11, True), 0, 2,
                       Qt.AlignCenter)

        self._notif_chks = {}   # ev_type → (chk_notify, chk_sound)
        for row_i, (ev_type, label) in enumerate(EVENT_LABELS.items(), 1):
            ev_l.addWidget(_lbl(label, C['txt2'], 12), row_i, 0)
            chk_n = QCheckBox()
            chk_n.setChecked(True)
            ev_l.addWidget(chk_n, row_i, 1, Qt.AlignCenter)
            chk_s = QCheckBox()
            chk_s.setChecked(True)
            ev_l.addWidget(chk_s, row_i, 2, Qt.AlignCenter)
            self._notif_chks[ev_type] = (chk_n, chk_s)

        vl.addWidget(events_grp)
        vl.addStretch()

        # Load existing notif settings
        if self._notif:
            self.chk_notif_enabled.setChecked(
                self._notif.settings.notifications_enabled)
            self.chk_sound_enabled.setChecked(
                self._notif.settings.sound_enabled)
            for ev_type, (chk_n, chk_s) in self._notif_chks.items():
                ev_cfg = self._notif.settings.events.get(ev_type)
                if ev_cfg:
                    chk_n.setChecked(ev_cfg.enabled)
                    chk_s.setChecked(ev_cfg.sound)

        scroll.setWidget(inner)
        return scroll

    # ── Tab 4: About ──────────────────────────────────────────────

    def _tab_about(self):
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setSpacing(14)
        vl.setContentsMargins(24, 24, 24, 24)

        icon = _lbl("📈", size=52)
        icon.setAlignment(Qt.AlignCenter)
        vl.addWidget(icon)

        title = _lbl("TraderBot v4", C['gold'], 22, True)
        title.setAlignment(Qt.AlignCenter)
        vl.addWidget(title)

        try:
            from core.updater import APP_VERSION
            ver_str = APP_VERSION
        except Exception:
            ver_str = "4.0.0"

        sub = _lbl(f"Version {ver_str}  ·  Rectangle-Anchored Recovery Bot",
                   C['txt2'], 12)
        sub.setAlignment(Qt.AlignCenter)
        vl.addWidget(sub)

        vl.addWidget(_divider())

        desc = QLabel(
            "ICT-based automated recovery trading bot for MetaTrader 5.\n\n"
            "Features: Soft lot modes 1/2/3 · Loss-free & Risk-free SL · "
            "ICT Multi-Timeframe Bias · Auto-updates · Trade reporting"
        )
        desc.setStyleSheet(f"color:{C['txt2']};font-size:12px;")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        vl.addWidget(desc)
        vl.addStretch()
        return w

    # ── Load / Save ───────────────────────────────────────────────

    def _load_profile(self):
        p = self._profile
        self.inp_name.setText(p.get("display_name", ""))
        self.inp_login.setText(str(p.get("mt5_login", "")))
        self.inp_pw.setText(p.get("mt5_password", ""))
        self.inp_server.setText(p.get("mt5_server", ""))
        self.inp_symbol.setText(p.get("watch_symbol", "EURUSD"))
        self.spin_lot.setValue(float(p.get("lot_size", 0.01)))
        self.combo_mode.setCurrentIndex(
            {1: 0, 2: 1, 3: 2}.get(int(p.get("soft_lot_mode", 1)), 0))
        self.spin_bal_tp.setValue(float(p.get("balance_tp_pct", 10.0)))
        self.spin_max_touch.setValue(int(p.get("max_touches", 12)))

    def _save(self):
        # Validate credentials
        login = self.inp_login.text().strip()
        if login:
            try:
                int(login)
            except ValueError:
                self.err_lbl.setText("⚠  Account login must be a number.")
                self._tabs.setCurrentIndex(0)
                return
        self.err_lbl.setText("")

        # Build profile dict
        self._profile.update({
            "display_name":  self.inp_name.text().strip(),
            "mt5_login":     login,
            "mt5_password":  self.inp_pw.text(),
            "mt5_server":    self.inp_server.text().strip(),
            "watch_symbol":  self.inp_symbol.text().strip() or "EURUSD",
            "lot_size":      self.spin_lot.value(),
            "soft_lot_mode": {0: 1, 1: 2, 2: 3}.get(
                self.combo_mode.currentIndex(), 1),
            "balance_tp_pct": self.spin_bal_tp.value(),
            "max_touches":   self.spin_max_touch.value(),
        })

        from core.profile import save_profile
        save_profile(self._profile)

        # Save notification settings
        if self._notif:
            self._notif.settings.notifications_enabled = (
                self.chk_notif_enabled.isChecked())
            self._notif.settings.sound_enabled = (
                self.chk_sound_enabled.isChecked())
            for ev_type, (chk_n, chk_s) in self._notif_chks.items():
                ev_cfg = self._notif.settings.events.get(ev_type)
                if ev_cfg:
                    ev_cfg.enabled = chk_n.isChecked()
                    ev_cfg.sound = chk_s.isChecked()

        self.profile_saved.emit(self._profile)
        self._status_lbl.setText("✅  Saved")
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self._status_lbl.setText(""))


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    dlg = AccountDialog()
    dlg.exec_()
