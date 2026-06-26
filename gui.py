"""
gui.py — TraderBot v4 entry point.

The GUI is now split into the gui/ package for maintainability.
This file exists only for backward compatibility (python gui.py still works).

To add a new feature:
  1. Create gui/panel_myfeature.py with class MyFeatureMixin
  2. Add it to the inheritance list in gui/app.py
  3. Done — no other file needs to change.
"""
from gui.app import GUI

if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("TraderBot v4")
    w = GUI()
    w.show()
    sys.exit(app.exec_())
