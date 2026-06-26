#!/usr/bin/env bash
# ============================================================
#  TraderBot v4 — Project Setup Script
#  Run once after cloning or re-structuring the project.
#  Creates every folder and empty file so you can paste code
#  into them without worrying about the structure.
#
#  Usage (Git Bash / WSL / Linux / macOS):
#    chmod +x setup_project.sh
#    ./setup_project.sh
# ============================================================

set -e

# ── Colors ────────────────────────────────────────────────────────
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GRAY='\033[0;37m'
NC='\033[0m'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

created=0
skipped=0

make_file() {
    local path="$1"
    local dir
    dir="$(dirname "$path")"
    mkdir -p "$dir"
    if [ ! -f "$path" ]; then
        touch "$path"
        echo -e "  ${GREEN}+ created${NC}  $path"
        created=$((created+1))
    else
        echo -e "  ${GRAY}  exists ${NC}  $path"
        skipped=$((skipped+1))
    fi
}

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  TraderBot v4 — Project Setup${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# ── Root level ────────────────────────────────────────────────────
echo -e "${YELLOW}[ Root files ]${NC}"
make_file "gui.py"
make_file "config.py"
make_file "setup_dialog.py"
make_file "account_dialog.py"
make_file "version.json"
make_file "requirements.txt"
make_file "build.bat"
make_file "traderbotv4.spec"
make_file "setup_installer.iss"
make_file "HOW_TO_BUILD_EXE.md"
make_file "README.md"
echo ""

# ── gui/ package ─────────────────────────────────────────────────
echo -e "${YELLOW}[ gui/ package ]${NC}"
make_file "gui/__init__.py"
make_file "gui/app.py"
make_file "gui/theme.py"
make_file "gui/widgets.py"
make_file "gui/shared_imports.py"
make_file "gui/core_init.py"
make_file "gui/panel_control.py"
make_file "gui/panel_detectors.py"
make_file "gui/panel_bias.py"
make_file "gui/panel_report.py"
make_file "gui/panel_right.py"
make_file "gui/handlers.py"
echo ""

# ── core/ package ─────────────────────────────────────────────────
echo -e "${YELLOW}[ core/ package ]${NC}"
make_file "core/__init__.py"
# Trading engine
make_file "core/watcher.py"
make_file "core/order_manager.py"
make_file "core/resume.py"
# Position state machine (6 mixins assembled in position_monitor.py)
make_file "core/position_monitor.py"
make_file "core/position_monitor_base.py"
make_file "core/position_entry.py"
make_file "core/position_geometry.py"
make_file "core/position_helpers.py"
make_file "core/position_protection.py"
make_file "core/position_recovery.py"
# Detectors
make_file "core/fvg_detector.py"
make_file "core/fvg_watcher.py"
make_file "core/ob_detector.py"
make_file "core/ob_watcher.py"
make_file "core/ob_fvg_confluence.py"
make_file "core/confluence_watcher.py"
make_file "core/mtf_fvg.py"
make_file "core/mtf_fvg_watcher.py"
make_file "core/amd_detector.py"
make_file "core/amd_watcher.py"
make_file "core/rect_suggest_detector.py"
make_file "core/rect_suggest_watcher.py"
make_file "core/bias_detector.py"
make_file "core/bias_watcher.py"
# Services
make_file "core/profile.py"
make_file "core/updater.py"
make_file "core/notifications.py"
make_file "core/trade_db.py"
echo ""

# ── logs/ folder ──────────────────────────────────────────────────
echo -e "${YELLOW}[ logs/ folder ]${NC}"
mkdir -p logs
echo -e "  ${GREEN}+ created${NC}  logs/"
echo ""

# ── Summary ───────────────────────────────────────────────────────
echo -e "${CYAN}============================================================${NC}"
echo -e "  ${GREEN}Created : $created files${NC}"
echo -e "  ${GRAY}Skipped : $skipped files (already exist)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""
echo -e "${YELLOW}Project structure:${NC}"
echo ""
echo "  tradingbot_v4/"
echo "  ├── gui.py                    ← entry point (python gui.py)"
echo "  ├── config.py                 ← all settings & constants"
echo "  ├── setup_dialog.py           ← first-run wizard"
echo "  ├── account_dialog.py         ← account & settings panel"
echo "  ├── version.json              ← auto-update manifest"
echo "  ├── requirements.txt"
echo "  ├── build.bat                 ← builds the EXE"
echo "  ├── traderbotv4.spec          ← PyInstaller spec"
echo "  ├── setup_installer.iss       ← Inno Setup installer script"
echo "  │"
echo "  ├── gui/                      ← GUI package (split for maintainability)"
echo "  │   ├── __init__.py           ← exports GUI class"
echo "  │   ├── app.py                ← assembles all mixins into GUI class"
echo "  │   ├── theme.py              ← color palette (C) + stylesheet (SS)"
echo "  │   ├── widgets.py            ← Sig, Sparkline, _stat_card, helpers"
echo "  │   ├── shared_imports.py     ← all external imports in one place"
echo "  │   ├── core_init.py          ← __init__, tray, update, profile"
echo "  │   ├── panel_control.py      ← left control panel"
echo "  │   ├── panel_detectors.py    ← detectors tab"
echo "  │   ├── panel_bias.py         ← ICT bias tab"
echo "  │   ├── panel_report.py       ← report tab + chart"
echo "  │   ├── panel_right.py        ← log / sources / orders tabs"
echo "  │   └── handlers.py           ← bot start/stop, all callbacks"
echo "  │"
echo "  ├── core/                     ← trading engine"
echo "  │   ├── watcher.py            ← main loop, rectangle detection"
echo "  │   ├── order_manager.py      ← MT5 order helpers"
echo "  │   ├── resume.py             ← session save/restore"
echo "  │   ├── position_monitor.py   ← SourceState assembled class"
echo "  │   ├── position_*.py         ← position state machine mixins"
echo "  │   ├── *_detector.py         ← FVG / OB / AMD / Bias detectors"
echo "  │   ├── *_watcher.py          ← background scanner threads"
echo "  │   ├── profile.py            ← credential storage"
echo "  │   ├── updater.py            ← auto-update engine"
echo "  │   ├── notifications.py      ← desktop notifications + sound"
echo "  │   └── trade_db.py           ← SQLite trade recorder"
echo "  │"
echo "  └── logs/                     ← log files written at runtime"
echo ""
echo -e "${GREEN}Done! Paste your code into each file and run: python gui.py${NC}"
echo ""