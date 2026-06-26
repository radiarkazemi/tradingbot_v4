"""
gui_pkg — TraderBot v4 GUI package

Split structure:
  theme.py           — Color palette (C), global stylesheet (SS)
  widgets.py         — Sig signal bridge, Sparkline, _stat_card, helpers
  panel_control.py   — Left control tab (settings, start/stop)
  panel_detectors.py — Detectors tab (FVG, OB, Confluence, AMD, Bias checkbox)
  panel_right.py     — Right panel shell + Log, Sources, Orders tabs
  panel_bias.py      — Bias tab (ICT MTF analysis table)
  panel_report.py    — Report tab (trade history, stats cards, chart)
  core_init.py       — GUI __init__, tray icon, auto-update, profile
  handlers.py        — Bot start/stop, all toggle/event handlers
  app.py             — GUI class assembled from all mixins + entry point

Usage:
  python -m gui_pkg        (runs the app directly)
  from gui_pkg import GUI  (import the class)
"""
from gui.app import GUI


def run():
    import sys
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("TraderBot v4")
    w = GUI()
    w.show()
    sys.exit(app.exec_())
