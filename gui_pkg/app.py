"""
gui_pkg/app.py — Main GUI class for TraderBot v4.

Assembles all mixin classes into the final GUI via multiple inheritance.
Keep this file thin — it only declares the class hierarchy and the
entry-point. All actual methods live in the dedicated panel/mixin files.

Inheritance order (MRO matters for super().__init__):
  GUI
    CoreInitMixin       ← __init__, tray, update, profile
    ControlPanelMixin   ← left panel, header, statusbar
    DetectorsPanelMixin ← detectors tab
    BiasPanelMixin      ← bias tab + handlers
    ReportPanelMixin    ← report tab + chart
    RightPanelMixin     ← right shell + log/sources/orders
    HandlersMixin       ← bot start/stop, all event callbacks
    QMainWindow         ← Qt base
"""

from PyQt5.QtWidgets import QMainWindow

from .core_init import CoreInitMixin
from .panel_control import ControlPanelMixin
from .panel_detectors import DetectorsPanelMixin
from .panel_bias import BiasPanelMixin
from .panel_report import ReportPanelMixin
from .panel_right import RightPanelMixin
from .handlers import HandlersMixin


class GUI(
    CoreInitMixin,
    ControlPanelMixin,
    DetectorsPanelMixin,
    BiasPanelMixin,
    ReportPanelMixin,
    RightPanelMixin,
    HandlersMixin,
    QMainWindow,
):
    """
    TraderBot v4 main window.

    All UI-building methods and event handlers are defined in the
    mixin files above — this class just wires them together.
    Add new features by creating a new mixin and adding it here.
    """
    pass


if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("TraderBot v4")
    w = GUI()
    w.show()
    sys.exit(app.exec_())
