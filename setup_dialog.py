"""
setup_dialog.py — First-run setup wizard and Settings dialog for TraderBot v4.

Shows a 3-step wizard on first launch:
  Step 1: Welcome
  Step 2: MT5 Credentials (login, password, server)
  Step 3: Preferences (symbol, base lot, lot mode)

Also accessible any time via the main GUI's Settings button.
"""

import sys
import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QDoubleSpinBox, QComboBox, QWidget, QStackedWidget,
    QFrame, QSizePolicy, QApplication, QProgressBar, QCheckBox,
    QSpacerItem,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QPixmap, QColor, QPainter, QLinearGradient

# ── Palette (matches main GUI) ────────────────────────────────────
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
    "cyan":     "#00BCD4",
    "blue":     "#2979FF",
    "orange":   "#FF8C00",
}

SS = f"""
QDialog, QWidget {{
    background: {C['bg']};
    color: {C['txt']};
    font-family: 'Segoe UI';
    font-size: 13px;
}}
QLabel {{ background: transparent; }}
QLineEdit, QDoubleSpinBox, QComboBox {{
    background: {C['input']};
    color: {C['txt']};
    border: 1px solid {C['border']};
    border-radius: 5px;
    padding: 7px 10px;
    min-height: 30px;
    font-size: 13px;
}}
QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {C['cyan']};
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {C['border']}; border: none; width: 18px;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {C['card']}; color: {C['txt']};
    selection-background-color: {C['border']};
}}
QPushButton {{
    background: {C['card']};
    color: {C['txt']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 8px 22px;
    font-size: 13px;
}}
QPushButton:hover {{ background: {C['border']}; border-color: {C['border_hi']}; }}
QPushButton:pressed {{ background: {C['bg']}; }}
QPushButton#btn_primary {{
    background: {C['green_dk']};
    color: {C['green']};
    border: 1px solid {C['green']};
    font-weight: bold;
    font-size: 14px;
    padding: 10px 30px;
}}
QPushButton#btn_primary:hover {{ background: {C['green']}; color: #000; }}
QPushButton#btn_primary:disabled {{ background: {C['card']}; color: {C['txt3']}; border-color: {C['border']}; }}
QPushButton#btn_back {{
    background: transparent;
    color: {C['txt2']};
    border: 1px solid {C['border']};
}}
QCheckBox {{ color: {C['txt2']}; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {C['border']}; border-radius: 3px;
    background: {C['input']};
}}
QCheckBox::indicator:checked {{ background: {C['cyan']}; border-color: {C['cyan']}; }}
QFrame#divider {{ background: {C['border']}; max-height: 1px; }}
QProgressBar {{
    background: {C['input']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    height: 4px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {C['cyan']};
    border-radius: 4px;
}}
"""


def _lbl(text, color=None, size=13, bold=False):
    l = QLabel(text)
    style = f"font-size:{size}px;"
    if color:
        style += f"color:{color};"
    if bold:
        style += "font-weight:bold;"
    l.setStyleSheet(style)
    return l


def _divider():
    f = QFrame()
    f.setObjectName("divider")
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{C['border']};border:none;")
    return f


def _field(placeholder="", password=False, width=None):
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    if password:
        e.setEchoMode(QLineEdit.Password)
    if width:
        e.setFixedWidth(width)
    return e


# ── Step pages ────────────────────────────────────────────────────

class StepWelcome(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setSpacing(18)
        vl.setContentsMargins(10, 10, 10, 10)

        # Logo / icon area
        icon_lbl = QLabel("📈")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("font-size:56px;background:transparent;")
        vl.addWidget(icon_lbl)

        title = _lbl("TraderBot v4", C['gold'], size=26, bold=True)
        title.setAlignment(Qt.AlignCenter)
        vl.addWidget(title)

        sub = _lbl("Rectangle-Anchored Recovery Bot", C['txt2'], size=13)
        sub.setAlignment(Qt.AlignCenter)
        vl.addWidget(sub)

        vl.addWidget(_divider())

        desc = QLabel(
            "Welcome! This quick setup takes about 60 seconds.\n\n"
            "You will need:\n"
            "  •  Your MT5 account login number\n"
            "  •  Your MT5 account password\n"
            "  •  Your broker's MT5 server address\n\n"
            "These are stored securely on this machine only\n"
            "and are never transmitted anywhere."
        )
        desc.setStyleSheet(
            f"color:{C['txt2']};font-size:13px;line-height:1.6;")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        vl.addWidget(desc)

        vl.addStretch()


class StepCredentials(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setSpacing(14)
        vl.setContentsMargins(10, 0, 10, 10)

        vl.addWidget(_lbl("MT5 Account Credentials",
                     C['txt'], size=16, bold=True))
        vl.addWidget(_lbl(
            "Enter your MetaTrader 5 account details.\n"
            "Find these in your broker's welcome email.",
            C['txt2'], size=12
        ))
        vl.addWidget(_divider())

        def _row(label, widget, hint=None):
            lbl = _lbl(label, C['txt2'], size=12)
            lbl.setFixedWidth(130)
            row = QHBoxLayout()
            row.addWidget(lbl)
            row.addWidget(widget)
            vl.addLayout(row)
            if hint:
                h = _lbl(f"  {hint}", C['txt3'], size=11)
                h.setContentsMargins(134, 0, 0, 0)
                vl.addWidget(h)

        self.display_name = _field("e.g. My Trading Account")
        _row("Display Name:", self.display_name,
             "Optional — shown in the title bar")

        self.login = _field("e.g.  12345678")
        self.login.setValidator(None)
        _row("Account Login:", self.login,
             "Your MT5 account number (digits only)")

        self.password = _field("Your MT5 password", password=True)
        self.show_pw = QCheckBox("Show password")
        self.show_pw.stateChanged.connect(
            lambda s: self.password.setEchoMode(
                QLineEdit.Normal if s else QLineEdit.Password))
        pw_row = QHBoxLayout()
        pw_row.addWidget(_lbl("Password:", C['txt2'], size=12))
        pw_row.addWidget(self.password)
        pw_row.addWidget(self.show_pw)
        vl.addLayout(pw_row)

        self.server = _field("e.g.  LiteFinance-MT5-Demo")
        _row("Server:", self.server,
             "Broker's MT5 server name (from MT5 login screen)")

        vl.addStretch()

        # Validation hint
        self.hint_lbl = _lbl("", C['red'], size=12)
        vl.addWidget(self.hint_lbl)

    def validate(self) -> str:
        """Returns '' if valid, else an error message."""
        if not self.login.text().strip():
            return "Account login is required."
        try:
            int(self.login.text().strip())
        except ValueError:
            return "Account login must be a number."
        if not self.password.text():
            return "Password is required."
        if not self.server.text().strip():
            return "Server address is required."
        return ""

    def get_values(self) -> dict:
        return {
            "display_name": self.display_name.text().strip(),
            "mt5_login":    self.login.text().strip(),
            "mt5_password": self.password.text(),
            "mt5_server":   self.server.text().strip(),
        }

    def set_values(self, profile: dict):
        self.display_name.setText(profile.get("display_name", ""))
        self.login.setText(str(profile.get("mt5_login", "")))
        self.password.setText(profile.get("mt5_password", ""))
        self.server.setText(profile.get("mt5_server", ""))


class StepPreferences(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setSpacing(14)
        vl.setContentsMargins(10, 0, 10, 10)

        vl.addWidget(_lbl("Trading Preferences", C['txt'], size=16, bold=True))
        vl.addWidget(_lbl(
            "Set your default trading parameters.\n"
            "You can change all of these in the main app at any time.",
            C['txt2'], size=12
        ))
        vl.addWidget(_divider())

        def _row(label, widget, hint=None):
            lbl = _lbl(label, C['txt2'], size=12)
            lbl.setFixedWidth(130)
            row = QHBoxLayout()
            row.addWidget(lbl)
            row.addWidget(widget)
            vl.addLayout(row)
            if hint:
                h = _lbl(f"  {hint}", C['txt3'], size=11)
                h.setContentsMargins(134, 0, 0, 0)
                vl.addWidget(h)

        self.symbol = _field("e.g. EURUSD or XAUUSD_o")
        _row("Default Symbol:", self.symbol,
             "The MT5 symbol name (including broker suffix if any)")

        self.base_lot = QDoubleSpinBox()
        self.base_lot.setRange(0.01, 100.0)
        self.base_lot.setSingleStep(0.01)
        self.base_lot.setValue(0.01)
        self.base_lot.setDecimals(2)
        _row("Base Lot Size:", self.base_lot,
             "Starting lot for each cycle (touch 0)")

        self.lot_mode = QComboBox()
        self.lot_mode.addItems([
            "Mode 1  —  +0.01 per touch, max 0.11 lot",
            "Mode 2  —  +0.02 per touch, max 0.20 lot",
            "Mode 3  —  Classic Martingale (2× doubling)",
        ])
        _row("Lot Mode:", self.lot_mode,
             "How lot size grows after each loss")

        vl.addStretch()

    def get_values(self) -> dict:
        return {
            "watch_symbol":  self.symbol.text().strip() or "EURUSD",
            "lot_size":      self.base_lot.value(),
            "soft_lot_mode": {0: 1, 1: 2, 2: 3}.get(
                self.lot_mode.currentIndex(), 1),
        }

    def set_values(self, profile: dict):
        self.symbol.setText(profile.get("watch_symbol", "EURUSD"))
        self.base_lot.setValue(float(profile.get("lot_size", 0.01)))
        mode = int(profile.get("soft_lot_mode", 1))
        self.lot_mode.setCurrentIndex({1: 0, 2: 1, 3: 2}.get(mode, 0))


# ── Main wizard dialog ────────────────────────────────────────────

class SetupDialog(QDialog):
    """
    Multi-step setup wizard. Call exec_() and check result() == QDialog.Accepted.
    Access .profile for the collected values.
    """

    profile_saved = pyqtSignal(dict)  # emitted when user saves

    STEPS = ["Welcome", "MT5 Account", "Preferences", "Done"]

    def __init__(self, parent=None, existing_profile: dict = None,
                 title="First-Time Setup"):
        super().__init__(parent)
        self.setWindowTitle(f"TraderBot v4 — {title}")
        self.setMinimumSize(520, 560)
        self.setMaximumSize(560, 640)
        self.setModal(True)
        self.setStyleSheet(SS)
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)

        self.profile = dict(existing_profile) if existing_profile else {}
        self._step = 0

        self._build_ui()
        if existing_profile:
            self._load_existing(existing_profile)
        self._update_step()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Header bar ────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(
            f"background:{C['panel']};border-bottom:1px solid {C['border']};")
        header.setFixedHeight(56)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 0, 20, 0)

        self._step_lbl = _lbl("Step 1 of 3", C['txt3'], size=11)
        hl.addWidget(self._step_lbl)
        hl.addStretch()
        self._step_title = _lbl("Welcome", C['gold'], size=14, bold=True)
        hl.addWidget(self._step_title)
        root.addWidget(header)

        # ── Progress bar ──────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        root.addWidget(self._progress)

        # ── Page stack ────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setContentsMargins(20, 16, 20, 8)

        self._p_welcome = StepWelcome()
        self._p_creds = StepCredentials()
        self._p_prefs = StepPreferences()
        self._p_done = self._build_done_page()

        self._stack.addWidget(self._p_welcome)
        self._stack.addWidget(self._p_creds)
        self._stack.addWidget(self._p_prefs)
        self._stack.addWidget(self._p_done)
        root.addWidget(self._stack, 1)

        # ── Bottom nav bar ────────────────────────────────────────
        nav = QWidget()
        nav.setStyleSheet(
            f"background:{C['panel']};border-top:1px solid {C['border']};")
        nav.setFixedHeight(64)
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(20, 0, 20, 0)
        nl.setSpacing(12)

        self._btn_back = QPushButton("← Back")
        self._btn_back.setObjectName("btn_back")
        self._btn_back.setFixedWidth(100)
        self._btn_back.clicked.connect(self._go_back)
        nl.addWidget(self._btn_back)

        nl.addStretch()

        self._btn_next = QPushButton("Next →")
        self._btn_next.setObjectName("btn_primary")
        self._btn_next.setFixedWidth(140)
        self._btn_next.clicked.connect(self._go_next)
        nl.addWidget(self._btn_next)

        root.addWidget(nav)

    def _build_done_page(self):
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setSpacing(18)
        vl.setContentsMargins(10, 10, 10, 10)

        icon = _lbl("✅", size=52)
        icon.setAlignment(Qt.AlignCenter)
        vl.addWidget(icon)

        t = _lbl("You're all set!", C['green'], size=22, bold=True)
        t.setAlignment(Qt.AlignCenter)
        vl.addWidget(t)

        self._done_summary = QLabel("")
        self._done_summary.setStyleSheet(
            f"color:{C['txt2']};font-size:13px;line-height:1.8;")
        self._done_summary.setAlignment(Qt.AlignCenter)
        self._done_summary.setWordWrap(True)
        vl.addWidget(self._done_summary)

        vl.addWidget(_divider())

        note = _lbl(
            "Your credentials are saved on this machine only.\n"
            "You can update them any time via Settings in the main app.",
            C['txt3'], size=11
        )
        note.setAlignment(Qt.AlignCenter)
        note.setWordWrap(True)
        vl.addWidget(note)

        vl.addStretch()
        return w

    def _load_existing(self, profile: dict):
        self._p_creds.set_values(profile)
        self._p_prefs.set_values(profile)

    def _update_step(self):
        titles = ["Welcome", "MT5 Account", "Preferences", "All Done"]
        total_steps = 3  # welcome, creds, prefs (done is not counted)

        self._stack.setCurrentIndex(self._step)
        self._step_title.setText(titles[self._step])

        if self._step == 0:
            self._step_lbl.setText("Step 1 of 3")
            self._progress.setValue(0)
            self._btn_back.setEnabled(False)
            self._btn_next.setText("Let's Start →")
        elif self._step == 1:
            self._step_lbl.setText("Step 2 of 3")
            self._progress.setValue(33)
            self._btn_back.setEnabled(True)
            self._btn_next.setText("Next →")
        elif self._step == 2:
            self._step_lbl.setText("Step 3 of 3")
            self._progress.setValue(66)
            self._btn_back.setEnabled(True)
            self._btn_next.setText("Save & Finish")
        elif self._step == 3:
            self._step_lbl.setText("Complete")
            self._progress.setValue(100)
            self._btn_back.setEnabled(False)
            self._btn_next.setText("Open TraderBot →")
            self._btn_next.setEnabled(True)
            self._update_done_summary()

    def _update_done_summary(self):
        creds = self._p_creds.get_values()
        prefs = self._p_prefs.get_values()
        name = creds.get(
            "display_name") or f"Account #{creds.get('mt5_login', '')}"
        server = creds.get("mt5_server", "")
        symbol = prefs.get("watch_symbol", "EURUSD")
        mode = prefs.get("soft_lot_mode", 1)
        lot = prefs.get("lot_size", 0.01)
        mode_str = {1: "Mode 1", 2: "Mode 2",
                    3: "Mode 3 (Martingale)"}.get(mode, "Mode 1")
        self._done_summary.setText(
            f"<b style='color:{C['txt']}'>{name}</b><br>"
            f"Server: {server}<br>"
            f"Symbol: {symbol}   |   Base lot: {lot:.2f}   |   {mode_str}"
        )

    def _go_next(self):
        if self._step == 0:
            self._step = 1

        elif self._step == 1:
            err = self._p_creds.validate()
            if err:
                self._p_creds.hint_lbl.setText(f"⚠  {err}")
                return
            self._p_creds.hint_lbl.setText("")
            self._step = 2

        elif self._step == 2:
            # Collect & save
            self.profile = {}
            self.profile.update(self._p_creds.get_values())
            self.profile.update(self._p_prefs.get_values())
            from core.profile import save_profile
            save_profile(self.profile)
            self.profile_saved.emit(self.profile)
            self._step = 3

        elif self._step == 3:
            self.accept()
            return

        self._update_step()

    def _go_back(self):
        if self._step > 0:
            self._step -= 1
            self._update_step()

    def closeEvent(self, event):
        # If user closes before finishing first-time setup, reject
        if self._step < 3 and not self.profile:
            self.reject()
        else:
            self.accept()
        event.accept()


# ── Standalone test ───────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    dlg = SetupDialog()
    if dlg.exec_() == QDialog.Accepted:
        print("Saved profile:", dlg.profile)
    sys.exit(0)
