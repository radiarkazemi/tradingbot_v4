"""
gui — TraderBot v4 GUI package

Files:
  theme.py           — Color palette (C) and global stylesheet (SS)
  widgets.py         — Sig, Sparkline, _stat_card, _vline, _hline
  shared_imports.py  — All external imports used across mixins
  core_init.py       — GUI __init__, tray, auto-update, profile
  panel_control.py   — Left control panel tab
  panel_detectors.py — Detectors tab
  panel_bias.py      — ICT Bias tab
  panel_report.py    — Report tab + chart
  panel_right.py     — Log / Sources / Orders tabs
  handlers.py        — Bot start/stop, all event handlers
  app.py             — GUI class assembled from all mixins
"""
# No imports here — avoids circular import.
# gui.py at root does: from gui.app import GUI
