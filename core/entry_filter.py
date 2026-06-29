"""
core/entry_filter.py — OB + FVG Confluence Entry Filter

When enabled, the bot only places a new order pair if there is an
active OB+FVG confluence zone overlapping the rectangle's near edge
(the edge that was just touched).

How it works:
  1. On every touch event, call check_ob_fvg_confluence_at_edge()
  2. This fetches live OB + FVG data and runs find_confluences()
  3. It looks for a confluence zone whose price range overlaps the
     touched edge of the rectangle within OVERLAP_PIPS tolerance
  4. Direction must match: touched bottom → need BULL confluence
                           touched top    → need BEAR confluence
  5. If found → entry is allowed, returns (True, zone, log_msg)
     If not   → entry is blocked, returns (False, None, log_msg)

The filter is deliberately permissive:
  - Overlap tolerance is configurable (default 15 pips)
  - If the OB/FVG data fetch fails, entry is ALLOWED (fail-open)
  - The filter only blocks, never moves, the entry price

Config keys (config.py):
  ENTRY_FILTER_OB_FVG       = True   (master toggle, also GUI)
  ENTRY_FILTER_OVERLAP_PIPS = 15.0   (how far the zone can be from edge)
  ENTRY_FILTER_MIN_SCORE    = 10.0   (minimum confluence score to qualify)
"""

import logging
from typing import Optional, Tuple

log = logging.getLogger("entry_filter")


def check_ob_fvg_confluence_at_edge(
    symbol:       str,
    pip_size:     float,
    edge_price:   float,        # rect_bottom (for bull) or rect_top (for bear)
    direction:    str,          # "BULL" or "BEAR"
    overlap_pips: float = 15.0,
    min_score:    float = 10.0,
    lookback:     int   = 200,
    min_impulse:  float = 3.0,
    min_fvg:      float = 1.5,
) -> Tuple[bool, Optional[object], str]:
    """
    Check if there is an OB+FVG confluence zone near the given edge.

    Returns:
        (allowed, zone_or_None, log_message)

        allowed=True  → place the order pair
        allowed=False → block the entry, log the reason
    """
    try:
        import MetaTrader5 as mt5
        from core.ob_detector  import detect_order_blocks
        from core.fvg_detector  import detect_fvgs
        from core.ob_fvg_confluence import find_confluences

        # Fetch candle data
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, lookback)
        if bars is None or len(bars) < 20:
            return True, None, "⚠️  Entry filter: cannot fetch bars — allowing entry"

        # Detect OBs and FVGs
        obs  = detect_order_blocks(bars, pip_size=pip_size,
                                   min_impulse_pips=min_impulse)
        fvgs = detect_fvgs(bars, pip_size=pip_size,
                           min_gap_pips=min_fvg)

        # Find confluence zones
        zones = find_confluences(obs, fvgs)
        if not zones:
            return (
                False, None,
                f"🚫  Entry filter: no OB+FVG confluence found near "
                f"{edge_price:.5f} — entry blocked"
            )

        # Look for a zone that:
        #   1. Has matching direction
        #   2. Overlaps the edge within tolerance
        #   3. Meets minimum score
        tolerance = overlap_pips * pip_size
        matching  = []
        for z in zones:
            if z.kind != direction:
                continue
            if z.score < min_score:
                continue
            # Overlap check: the zone must overlap or be within tolerance of edge
            zone_top = z.combined_top
            zone_bot = z.combined_bottom
            # For BULL: edge is rect_bottom — zone should be at or near that level
            # For BEAR: edge is rect_top   — zone should be at or near that level
            if direction == "BULL":
                dist = abs(zone_bot - edge_price)
            else:
                dist = abs(zone_top - edge_price)
            if dist <= tolerance:
                matching.append((dist, z))

        if not matching:
            return (
                False, None,
                f"🚫  Entry filter: {len(zones)} confluence zone(s) found "
                f"but none within {overlap_pips:.0f} pips of edge "
                f"{edge_price:.5f} ({direction}) — entry blocked"
            )

        # Best match = closest to edge with highest score
        matching.sort(key=lambda x: (x[0], -x[1].score))
        best_dist, best_zone = matching[0]

        return (
            True, best_zone,
            f"✅  Entry filter: {direction} OB+FVG confluence "
            f"found {best_dist/pip_size:.1f}pips from edge | "
            f"score={best_zone.score:.1f} — entry ALLOWED\n"
            f"   {best_zone.summary()}"
        )

    except Exception as e:
        log.warning("Entry filter error (fail-open): %s", e)
        return True, None, f"⚠️  Entry filter error ({e}) — allowing entry"