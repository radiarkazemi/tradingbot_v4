"""
license_dialog.py — License activation dialog for TraderBot v4.
Shown on first run or when license is invalid/expired.
"""

import sys
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame, QWidget,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

C = {
    "bg":       "#0D1117", "panel":    "#161B22", "card":     "#1C2333",
    "input":    "#141D2E", "border":   "#2A3550", "border_hi": "#4A6090",
    "txt":      "#E8EDF5", "txt2":     "#8B9BB4", "txt3":     "#4A5568",
    "gold":     "#F5A623", "green":    "#00D97E", "green_dk": "#003D22",
    "red":      "#FF4560", "cyan":     "#00BCD4",
}

SS = f"""
QDialog, QWidget {{ background:{C['bg']}; color:{C['txt']};
    font-family:'Segoe UI'; font-size:13px; }}
QLabel {{ background:transparent; }}
QTextEdit {{
    background:{C['input']}; color:{C['txt']};
    border:1px solid {C['border']}; border-radius:5px;
    padding:8px; font-family:'Consolas'; font-size:12px;
}}
QTextEdit:focus {{ border-color:{C['cyan']}; }}
QPushButton {{
    background:{C['card']}; color:{C['txt']};
    border:1px solid {C['border']}; border-radius:6px;
    padding:8px 20px; font-size:13px;
}}
QPushButton:hover {{ background:{C['border']}; border-color:{C['border_hi']}; }}
QPushButton#btn_activate {{
    background:{C['green_dk']}; color:{C['green']};
    border:1px solid {C['green']}; font-weight:bold;
    font-size:14px; padding:10px 30px;
}}
QPushButton#btn_activate:hover {{ background:{C['green']}; color:#000; }}
QPushButton#btn_activate:disabled {{
    background:{C['card']}; color:{C['txt3']}; border-color:{C['border']};
}}
QFrame#divider {{ background:{C['border']}; max-height:1px; border:none; }}
"""


class LicenseDialog(QDialog):
    """
    Shown when no valid license is found.
    User pastes their key and clicks Activate.
    """

    def __init__(self, parent=None, reason: str = ""):
        super().__init__(parent)
        self.setWindowTitle("TraderBot v4 — License Activation")
        self.setMinimumSize(520, 440)
        self.setMaximumSize(560, 480)
        self.setModal(True)
        self.setStyleSheet(SS)
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowTitleHint)
        # Block close button — must activate or quit
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowTitleHint | Qt.CustomizeWindowHint)

        self._reason = reason
        self._activated = False
        self._build_ui()

    def _lbl(self, text, color=None, size=13, bold=False):
        l = QLabel(text)
        s = f"font-size:{size}px;"
        if color: s += f"color:{color};"
        if bold:  s += "font-weight:bold;"
        l.setStyleSheet(s)
        return l

    def _divider(self):
        f = QFrame()
        f.setObjectName("divider")
        f.setFrameShape(QFrame.HLine)
        f.setFixedHeight(1)
        f.setStyleSheet(f"background:{C['border']};border:none;")
        return f

    def _build_ui(self):
        vl = QVBoxLayout(self)
        vl.setSpacing(0)
        vl.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(64)
        hdr.setStyleSheet(
            f"background:{C['panel']};border-bottom:1px solid {C['border']};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(20, 0, 20, 0)
        icon = QLabel("🔐")
        icon.setStyleSheet("font-size:26px;")
        hl.addWidget(icon)
        hl.addSpacing(10)
        title_col = QVBoxLayout()
        title_col.addWidget(
            self._lbl("TraderBot v4", C['gold'], 15, True))
        title_col.addWidget(
            self._lbl("License Activation", C['txt2'], 11))
        hl.addLayout(title_col)
        hl.addStretch()
        vl.addWidget(hdr)

        # Body
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(12)
        bl.setContentsMargins(24, 20, 24, 20)

        # Reason banner
        if self._reason:
            banner = QLabel(f"  ⚠  {self._reason}")
            banner.setStyleSheet(
                f"background:#2a1a0a;color:{C['gold']};"
                f"border:1px solid {C['gold']}33;"
                f"border-radius:4px;padding:8px 12px;font-size:12px;")
            banner.setWordWrap(True)
            bl.addWidget(banner)

        bl.addWidget(self._lbl(
            "Enter your license key below.", C['txt2'], 12))
        bl.addWidget(self._lbl(
            "Contact the developer if you don't have a key yet.",
            C['txt3'], 11))

        bl.addWidget(self._divider())

        bl.addWidget(self._lbl("License Key:", C['txt2'], 12))
        self._key_edit = QTextEdit()
        self._key_edit.setPlaceholderText("Paste your license key here…")
        self._key_edit.setFixedHeight(100)
        self._key_edit.textChanged.connect(self._on_key_changed)
        bl.addWidget(self._key_edit)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color:{C['red']};font-size:12px;")
        self._status_lbl.setWordWrap(True)
        bl.addWidget(self._status_lbl)

        bl.addStretch()
        vl.addWidget(body, 1)

        # Footer
        ftr = QWidget()
        ftr.setFixedHeight(64)
        ftr.setStyleSheet(
            f"background:{C['panel']};border-top:1px solid {C['border']};")
        fl = QHBoxLayout(ftr)
        fl.setContentsMargins(20, 0, 20, 0)
        fl.setSpacing(12)

        quit_btn = QPushButton("Quit")
        quit_btn.setFixedWidth(90)
        quit_btn.clicked.connect(self._on_quit)
        fl.addWidget(quit_btn)

        fl.addStretch()

        self._activate_btn = QPushButton("Activate")
        self._activate_btn.setObjectName("btn_activate")
        self._activate_btn.setFixedWidth(130)
        self._activate_btn.setEnabled(False)
        self._activate_btn.clicked.connect(self._on_activate)
        fl.addWidget(self._activate_btn)
        vl.addWidget(ftr)

    def _on_key_changed(self):
        text = self._key_edit.toPlainText().strip()
        self._activate_btn.setEnabled(len(text) > 20)
        self._status_lbl.setText("")

    def _on_activate(self):
        from core.license import activate_license
        key = self._key_edit.toPlainText().strip()
        self._activate_btn.setEnabled(False)
        self._activate_btn.setText("Activating…")

        success, msg = activate_license(key)

        if success:
            self._status_lbl.setStyleSheet(
                f"color:{C['green']};font-size:12px;")
            self._status_lbl.setText(f"✅  {msg}")
            self._activated = True
            QTimer.singleShot(1200, self.accept)
        else:
            self._status_lbl.setStyleSheet(
                f"color:{C['red']};font-size:12px;")
            self._status_lbl.setText(f"❌  {msg}")
            self._activate_btn.setEnabled(True)
            self._activate_btn.setText("Activate")

    def _on_quit(self):
        import sys
        sys.exit(0)

    def is_activated(self) -> bool:
        return self._activated


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    dlg = LicenseDialog(reason="No license found on this machine.")
    dlg.exec_()