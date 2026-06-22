"""
╔══════════════════════════════════════════════════════════════════╗
║         TraderBot v4 — Configuration                            ║
║         Rectangle-anchored 2-Leg Recovery Bot                   ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ── MT5 CREDENTIALS ──────────────────────────────────────────────
MT5_LOGIN = 91246510
MT5_PASSWORD = "@Radiar9841@"
MT5_SERVER = "LiteFinance-MT5-Demo"

# MT5_LOGIN = 52936622
# MT5_PASSWORD = "@Radiar9841@"
# MT5_SERVER = "Alpari-MT5-Demo"


# ── SYMBOL TO WATCH ──────────────────────────────────────────────
WATCH_SYMBOL = "EURUSD_o"

# ── SCAN SETTINGS ────────────────────────────────────────────────
SCAN_INTERVAL_SEC = 2

# ── ORDER SETTINGS ───────────────────────────────────────────────
# NOTE: there is no ORDER_DISTANCE_PIPS anymore. In v4 the entry/SL
# geometry comes directly from the trader-drawn rectangle's top and
# bottom edges (see core/position_monitor.py SourceState). The
# rectangle height IS the distance — nothing to configure.

LOT_SIZE = 0.01      # base lot — used as table index 0 ("start")
MAGIC_NUMBER = 998877

# ── SOFT LOT TABLE (item 4) ───────────────────────────────────────
# Index 0 = "start" (the very first entry, both legs).
# Index 1..11 = lot at the Nth touch (Nth time a leg either activates
# and bumps its still-pending opposite, or closes via SL and a new
# recovery stop is placed) — see SourceState._next_table_lot().
# Reaching past index 11 (a 12th touch would be required) trips the
# kill switch instead of placing another order — see MAX_TOUCHES.
SOFT_LOT_MODE = 1   # 1, 2, or 3 — selectable in the GUI

SOFT_LOT_TABLE_MODE1 = [0.01, 0.01, 0.02, 0.03, 0.04, 0.05,
                        0.06, 0.07, 0.08, 0.09, 0.10, 0.11]

SOFT_LOT_TABLE_MODE2 = [0.01, 0.01, 0.02, 0.04, 0.06, 0.08,
                        0.10, 0.12, 0.14, 0.16, 0.18, 0.20]

# Mode 3 = Classic Martingale — the original doubling formula this
# project ran before the soft-lot tables existed. NOT table-driven:
# every touch doubles whatever lot it's based on (round(x*2, 2),
# floor 0.01), with NO MAX_TOUCHES kill switch — exactly like before,
# this mode runs until balance TP (R3), the deep-round OB+FVG
# bounce-confluence gate (kicks in once lot >= 0.64) declines to
# continue, or margin protection can't even afford the minimum lot.
# Kept available because the soft-lot tables intentionally trade away
# some of this mode's recovery power for a much lower risk ceiling —
# you may want the old behavior back for comparison/backtesting.
LOT_MODE_MARTINGALE = 3

MAX_TOUCHES = 11    # touch_count > this -> kill switch. Modes 1/2 ONLY
# (mode 3 has no touch cap, matching its original
# behavior, and is bounded only by margin/confluence
# gating + the account-level hard stop-loss below).

# ── R1 / R2 / R3 (item 8) ─────────────────────────────────────────
# R1 = Loss-Free: once floating profit reaches LOSS_FREE_TRIGGER_R,
#      move SL to breakeven (entry price) - never lose money on a
#      round that has already moved in our favor.
# R2 = Risk-Free: once floating profit reaches RISK_FREE_TRIGGER_R,
#      move SL to lock in cumulative_loss + this round's risk (same
#      mechanism as v3's single risk-free, just renamed/numbered).
# R3 = Take-Profit: the existing balance-target TP (unchanged math).
LOSS_FREE_TRIGGER_R = 1.0
RISK_FREE_TRIGGER_R = 2.0

# ── Partial Exit (scale-out at R2) ────────────────────────────────
# When R2 (risk-free) triggers, close PARTIAL_EXIT_RATIO of the
# position's volume immediately (banking real profit) and apply the
# usual risk-free SL lock to the remaining volume, which keeps
# running toward R3 (TP).
#
# NOTE on a real bug from an earlier prototype of this feature: the
# shrunk volume after a partial close must NEVER be used as the basis
# for sizing the NEXT round's lot. In v4 this can't happen by
# construction — next-round lot always comes from the fixed soft-lot
# table indexed by touch_count (see SourceState._next_table_lot),
# never derived from a previous round's live position volume. If
# that ever changes, re-verify this isn't reintroduced.
PARTIAL_EXIT_ENABLED = True
PARTIAL_EXIT_RATIO = 0.70   # close this fraction of volume at R2

# Balance-target TP (R3) - bot closes everything & stops once account
# balance reaches start_balance * BALANCE_TP_RATIO.
BALANCE_TP_RATIO = 1.10

# ── HARD KILL SWITCH (item 1 / queued feature #1) ─────────────────
# Account-level circuit breaker, independent of MAX_TOUCHES. If
# account equity drops to start_balance * (1 - HARD_STOP_LOSS_RATIO)
# at ANY point, every position/order for the watched symbol is
# closed and the bot halts completely. This is the absolute floor -
# MAX_TOUCHES is expected to trip first in normal operation, this is
# the backstop for cases where it doesn't (e.g. several sources
# losing concurrently).
HARD_STOP_LOSS_RATIO = 0.50   # 50% of start balance - TUNE BEFORE LIVE USE

# ── OBJECT FILTERING ─────────────────────────────────────────────
# CRITICAL: every prefix used by any detector/drawer in this bot
# MUST be listed here, or the watcher will treat its own drawn
# rectangles/labels as trader-drawn signal rectangles and start
# trading on them automatically. (See "AUTO_OBJECT_PREFIXES is a
# mandatory checklist item" - this has bitten this project 3 times
# already; do not skip it for new features.)
AUTO_OBJECT_PREFIXES = [
    "PA_", "CT", "GB_", "TB4_", "autotrade",
    "FVG_",       # FVG detector rectangles
    "OB_",        # Order Block rectangles
    "OBFVG_",     # OB+FVG Confluence rectangles
    "AMD_",       # AMD Quarter Theory boxes
    "AMDT_",      # AMD info table labels
    "MTFFVG_",    # Multi-timeframe FVG confluence intersection zones
    "MTFFVG5M_",  # Multi-timeframe FVG 5M entry rectangles
    "TB4_R1_",    # Bot-drawn movable Loss-Free line (thin rectangle)
    "TB4_R2_",    # Bot-drawn movable Risk-Free line (thin rectangle)
]

BOT_LINE_PREFIX = "TB4_"

# ── LOGGING ──────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
