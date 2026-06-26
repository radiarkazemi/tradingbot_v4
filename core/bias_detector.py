"""
core/bias_detector.py — ICT Multi-Timeframe Bias Analyzer
==========================================================
Computes the probability that price is in a BULLISH or BEARISH
context on each timeframe from M1 to H1 using a weighted ICT
signal stack.

ICT parameters used (each contributes a weighted vote):
──────────────────────────────────────────────────────────
1. Market Structure  (weight 3.0)
   BOS (Break of Structure): higher-high + higher-low = bullish
   CHoCH (Change of Character): lower-high + lower-low = bearish
   Uses swing highs/lows with configurable lookback.

2. Premium / Discount Arrays  (weight 2.0)
   Equilibrium = 50% of the visible price range.
   Price below EQ  → discount zone → bullish bias (ICT buys discount)
   Price above EQ  → premium zone  → bearish bias (ICT sells premium)

3. Fair Value Gaps  (weight 2.0)
   Net imbalance of unfilled bullish vs bearish FVGs in lookback window.
   More open BULL FVGs below price → bullish institutional interest.
   More open BEAR FVGs above price → bearish institutional interest.

4. Order Blocks  (weight 2.5)
   Nearest unmitigated OB below price = bullish support.
   Nearest unmitigated OB above price = bearish resistance.
   Both present → neutral. Nearest wins.

5. Previous Day / Session High-Low  (weight 1.5)
   Price above PDH (previous day high) → bullish continuation.
   Price below PDL (previous day low)  → bearish continuation.
   Between PDH and PDL → neutral / consolidation.

6. Momentum (EMA slope)  (weight 1.0)
   20-period EMA slope over last 3 bars.
   Rising EMA → bullish momentum. Falling → bearish.

Total possible weight = 12.0
Score > 0 = net bullish votes
Score < 0 = net bearish votes
Probability = sigmoid(score / 3.0) mapped to 0..100%
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import MetaTrader5 as mt5

log = logging.getLogger("bias_detector")

# ── Timeframes scanned ────────────────────────────────────────────
BIAS_TIMEFRAMES = [
    mt5.TIMEFRAME_M1,
    mt5.TIMEFRAME_M5,
    mt5.TIMEFRAME_M15,
    mt5.TIMEFRAME_M30,
    mt5.TIMEFRAME_H1,
]

TF_NAMES = {
    mt5.TIMEFRAME_M1:  "M1",
    mt5.TIMEFRAME_M5:  "M5",
    mt5.TIMEFRAME_M15: "M15",
    mt5.TIMEFRAME_M30: "M30",
    mt5.TIMEFRAME_H1:  "H1",
}

# Signal weights
W_STRUCTURE = 3.0
W_PD_ARRAY = 2.0
W_FVG = 2.0
W_OB = 2.5
W_PDH_PDL = 1.5
W_MOMENTUM = 1.0
TOTAL_WEIGHT = W_STRUCTURE + W_PD_ARRAY + W_FVG + W_OB + W_PDH_PDL + W_MOMENTUM


@dataclass
class SignalDetail:
    name:    str
    vote:    float   # -1.0 .. +1.0
    weight:  float
    reason:  str     # short human-readable explanation


@dataclass
class TimeframeBias:
    tf:          int
    tf_name:     str
    bull_pct:    float          # 0..100
    bear_pct:    float          # 0..100 (= 100 - bull_pct)
    direction:   str            # "BULL", "BEAR", or "NEUTRAL"
    confidence:  str            # "Strong", "Moderate", "Weak"
    score:       float          # raw weighted score
    signals:     List[SignalDetail] = field(default_factory=list)

    @property
    def emoji(self) -> str:
        if self.direction == "BULL":
            return "🟢"
        if self.direction == "BEAR":
            return "🔴"
        return "⚪"


def _get_pip_size(symbol: str) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        s = symbol.upper()
        if "JPY" in s:
            return 0.01
        if "XAU" in s:
            return 0.10
        if "XAG" in s:
            return 0.01
        return 0.0001
    if info.digits in (0, 1):
        return 1.0
    if info.digits in (2, 3):
        return info.point * 10
    return info.point * 10


def _sigmoid(x: float) -> float:
    """Maps any real → (0, 1). Used to convert weighted score to probability."""
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _ema(values: list, period: int) -> list:
    """Simple EMA calculation."""
    if not values or period <= 0:
        return values
    k = 2.0 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


# ── Individual signal detectors ───────────────────────────────────

def _signal_market_structure(bars, pip: float) -> SignalDetail:
    """
    Detects Break of Structure (BOS) and Change of Character (CHoCH)
    using the last 20 bars' swing highs and lows.
    """
    if len(bars) < 10:
        return SignalDetail("Structure", 0.0, W_STRUCTURE, "Not enough data")

    highs = [float(b["high"]) for b in bars[-20:]]
    lows = [float(b["low"]) for b in bars[-20:]]

    # Find 3 most recent swing highs and lows (local max/min with 2-bar window)
    swing_highs = []
    swing_lows = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] \
                and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] \
                and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return SignalDetail("Structure", 0.0, W_STRUCTURE, "Insufficient swings")

    # Higher highs + higher lows = bullish structure
    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1] > swing_lows[-2]
    # Lower highs + lower lows = bearish structure
    lh = swing_highs[-1] < swing_highs[-2]
    ll = swing_lows[-1] < swing_lows[-2]

    if hh and hl:
        return SignalDetail("Structure (BOS)", +1.0, W_STRUCTURE,
                            f"HH+HL: bullish BOS "
                            f"(HH {swing_highs[-2]:.4f}→{swing_highs[-1]:.4f})")
    if lh and ll:
        return SignalDetail("Structure (CHoCH)", -1.0, W_STRUCTURE,
                            f"LH+LL: bearish CHoCH "
                            f"(LL {swing_lows[-2]:.4f}→{swing_lows[-1]:.4f})")
    if hh and not hl:
        return SignalDetail("Structure", +0.4, W_STRUCTURE,
                            "HH only — partial bullish structure")
    if ll and not lh:
        return SignalDetail("Structure", -0.4, W_STRUCTURE,
                            "LL only — partial bearish structure")
    return SignalDetail("Structure", 0.0, W_STRUCTURE,
                        "No clear BOS/CHoCH — consolidation")


def _signal_pd_array(bars, current_price: float) -> SignalDetail:
    """
    Premium/Discount: current price vs 50% (equilibrium) of lookback range.
    """
    if len(bars) < 5:
        return SignalDetail("Premium/Discount", 0.0, W_PD_ARRAY, "Not enough data")

    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    hi = max(highs)
    lo = min(lows)
    rng = hi - lo
    if rng < 1e-8:
        return SignalDetail("Premium/Discount", 0.0, W_PD_ARRAY, "Flat range")

    eq = lo + rng * 0.5
    pos = (current_price - lo) / rng  # 0 = at low, 1 = at high

    if pos < 0.35:
        # Deep discount — strong bullish signal
        vote = +1.0
        reason = f"Deep discount ({pos*100:.0f}% of range) — ICT buy zone"
    elif pos < 0.50:
        vote = +0.5
        reason = f"Discount ({pos*100:.0f}% of range) — below equilibrium"
    elif pos > 0.65:
        # Premium — strong bearish signal
        vote = -1.0
        reason = f"Premium ({pos*100:.0f}% of range) — ICT sell zone"
    elif pos > 0.50:
        vote = -0.5
        reason = f"Slight premium ({pos*100:.0f}% of range) — above equilibrium"
    else:
        vote = 0.0
        reason = f"At equilibrium ({pos*100:.0f}% of range)"

    return SignalDetail("Premium/Discount", vote, W_PD_ARRAY, reason)


def _signal_fvg(bars, current_price: float, pip: float) -> SignalDetail:
    """
    Count unfilled bullish FVGs below price vs bearish FVGs above price.
    Net imbalance drives the vote.
    """
    if len(bars) < 3:
        return SignalDetail("FVG balance", 0.0, W_FVG, "Not enough data")

    min_gap = pip * 1.5
    bull_fvgs_below = 0
    bear_fvgs_above = 0

    for i in range(len(bars) - 2):
        left = bars[i]
        right = bars[i + 2]
        l_high = float(left["high"])
        l_low = float(left["low"])
        r_high = float(right["high"])
        r_low = float(right["low"])

        # Bullish FVG: gap = right.low > left.high
        if r_low > l_high and (r_low - l_high) >= min_gap:
            mid = (r_low + l_high) / 2
            if mid < current_price:  # unmitigated support below
                bull_fvgs_below += 1

        # Bearish FVG: gap = right.high < left.low
        elif r_high < l_low and (l_low - r_high) >= min_gap:
            mid = (r_high + l_low) / 2
            if mid > current_price:  # unmitigated resistance above
                bear_fvgs_above += 1

    total = bull_fvgs_below + bear_fvgs_above
    if total == 0:
        return SignalDetail("FVG balance", 0.0, W_FVG,
                            "No unmitigated FVGs in range")

    net = bull_fvgs_below - bear_fvgs_above
    vote = max(-1.0, min(1.0, net / max(total, 3)))
    direction = "bullish" if net > 0 else ("bearish" if net < 0 else "neutral")
    return SignalDetail("FVG balance", vote, W_FVG,
                        f"{bull_fvgs_below} bull FVGs below, "
                        f"{bear_fvgs_above} bear FVGs above → {direction}")


def _signal_order_blocks(bars, current_price: float, pip: float) -> SignalDetail:
    """
    Find the nearest unmitigated OB above and below current price.
    Nearest wins. An OB = last opposite-color candle before an impulse.
    """
    if len(bars) < 6:
        return SignalDetail("Order Blocks", 0.0, W_OB, "Not enough data")

    min_impulse = pip * 3.0
    nearest_bull_ob = None   # (distance, high, low)
    nearest_bear_ob = None

    for i in range(1, len(bars) - 2):
        c0 = bars[i]       # candidate OB candle
        c1 = bars[i + 1]   # impulse candle

        o0 = float(c0["open"])
        c0_close = float(c0["close"])
        o1 = float(c1["open"])
        c1_close = float(c1["close"])
        c0_high = float(c0["high"])
        c0_low = float(c0["low"])
        c1_high = float(c1["high"])
        c1_low = float(c1["low"])

        # Bullish OB: bearish candle followed by strong bullish impulse
        if c0_close < o0 and c1_close > o1:
            impulse = c1_high - o1
            if impulse >= min_impulse:
                # OB zone = c0's low..high
                zone_mid = (c0_high + c0_low) / 2
                if zone_mid < current_price:  # OB is below — support
                    dist = current_price - zone_mid
                    if nearest_bull_ob is None or dist < nearest_bull_ob[0]:
                        nearest_bull_ob = (dist, c0_high, c0_low)

        # Bearish OB: bullish candle followed by strong bearish impulse
        elif c0_close > o0 and c1_close < o1:
            impulse = o1 - c1_low
            if impulse >= min_impulse:
                zone_mid = (c0_high + c0_low) / 2
                if zone_mid > current_price:  # OB is above — resistance
                    dist = zone_mid - current_price
                    if nearest_bear_ob is None or dist < nearest_bear_ob[0]:
                        nearest_bear_ob = (dist, c0_high, c0_low)

    if nearest_bull_ob and nearest_bear_ob:
        # Both present — nearest wins
        if nearest_bull_ob[0] < nearest_bear_ob[0]:
            return SignalDetail("Order Blocks", +0.6, W_OB,
                                f"Nearest OB is BULL support "
                                f"({nearest_bull_ob[0]/pip:.1f}p below)")
        else:
            return SignalDetail("Order Blocks", -0.6, W_OB,
                                f"Nearest OB is BEAR resistance "
                                f"({nearest_bear_ob[0]/pip:.1f}p above)")
    if nearest_bull_ob:
        return SignalDetail("Order Blocks", +1.0, W_OB,
                            f"BULL OB support {nearest_bull_ob[0]/pip:.1f}p below, "
                            f"no bear OB above")
    if nearest_bear_ob:
        return SignalDetail("Order Blocks", -1.0, W_OB,
                            f"BEAR OB resistance {nearest_bear_ob[0]/pip:.1f}p above, "
                            f"no bull OB below")
    return SignalDetail("Order Blocks", 0.0, W_OB, "No OBs detected in range")


def _signal_pdh_pdl(symbol: str, current_price: float, pip: float) -> SignalDetail:
    """
    Previous Day High/Low: where is current price relative to yesterday's range.
    Uses daily bars.
    """
    try:
        daily = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 3)
        if daily is None or len(daily) < 2:
            return SignalDetail("PDH/PDL", 0.0, W_PDH_PDL, "No daily data")
        # bars[0] = oldest, bars[-1] = current forming day
        # bars[-2] = previous completed day
        pdh = float(daily[-2]["high"])
        pdl = float(daily[-2]["low"])

        if current_price > pdh:
            return SignalDetail("PDH/PDL", +1.0, W_PDH_PDL,
                                f"Above PDH ({pdh:.4f}) — bullish continuation")
        if current_price < pdl:
            return SignalDetail("PDH/PDL", -1.0, W_PDH_PDL,
                                f"Below PDL ({pdl:.4f}) — bearish continuation")
        mid = (pdh + pdl) / 2
        pos = (current_price - pdl) / (pdh - pdl + 1e-10)
        if pos > 0.55:
            return SignalDetail("PDH/PDL", -0.3, W_PDH_PDL,
                                f"Inside PDH/PDL range, upper half — mild bearish")
        if pos < 0.45:
            return SignalDetail("PDH/PDL", +0.3, W_PDH_PDL,
                                f"Inside PDH/PDL range, lower half — mild bullish")
        return SignalDetail("PDH/PDL", 0.0, W_PDH_PDL,
                            f"At PDH/PDL midpoint — neutral")
    except Exception as e:
        return SignalDetail("PDH/PDL", 0.0, W_PDH_PDL, f"Error: {e}")


def _signal_momentum(bars) -> SignalDetail:
    """
    20-EMA slope: rising = bullish, falling = bearish.
    Uses close prices of available bars.
    """
    if len(bars) < 5:
        return SignalDetail("EMA Momentum", 0.0, W_MOMENTUM, "Not enough data")

    closes = [float(b["close"]) for b in bars]
    period = min(20, len(closes))
    ema = _ema(closes, period)

    if len(ema) < 3:
        return SignalDetail("EMA Momentum", 0.0, W_MOMENTUM, "EMA too short")

    # Slope = change over last 3 bars, normalized by current level
    slope = (ema[-1] - ema[-3]) / (ema[-1] + 1e-10)

    if slope > 0.0003:
        return SignalDetail("EMA Momentum", +1.0, W_MOMENTUM,
                            f"EMA({period}) rising — bullish momentum")
    if slope < -0.0003:
        return SignalDetail("EMA Momentum", -1.0, W_MOMENTUM,
                            f"EMA({period}) falling — bearish momentum")
    if slope > 0:
        return SignalDetail("EMA Momentum", +0.3, W_MOMENTUM,
                            f"EMA({period}) flat-rising — weak bullish")
    return SignalDetail("EMA Momentum", -0.3, W_MOMENTUM,
                        f"EMA({period}) flat-falling — weak bearish")


# ── Main analysis function ────────────────────────────────────────

def analyze_bias(symbol: str,
                 timeframe: int,
                 lookback: int = 100) -> Optional[TimeframeBias]:
    """
    Run all ICT signal detectors on the given timeframe and return a
    TimeframeBias with bull/bear probability and individual signal details.
    Returns None if MT5 data is unavailable.
    """
    try:
        mt5.symbol_select(symbol, True)
        bars = mt5.copy_rates_from_pos(symbol, timeframe, 0, lookback)
        if bars is None or len(bars) < 10:
            return None

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None

        current_price = (tick.bid + tick.ask) / 2.0
        pip = _get_pip_size(symbol)
        tf_name = TF_NAMES.get(timeframe, str(timeframe))

        signals = [
            _signal_market_structure(bars, pip),
            _signal_pd_array(bars, current_price),
            _signal_fvg(bars, current_price, pip),
            _signal_order_blocks(bars, current_price, pip),
            _signal_pdh_pdl(symbol, current_price, pip),
            _signal_momentum(bars),
        ]

        # Weighted sum
        score = sum(s.vote * s.weight for s in signals)

        # Map to probability via sigmoid, scaled so ±TOTAL_WEIGHT maps near 0/100
        prob_bull = _sigmoid(score / (TOTAL_WEIGHT * 0.35)) * 100.0
        prob_bull = max(1.0, min(99.0, prob_bull))
        prob_bear = 100.0 - prob_bull

        # Direction threshold
        if prob_bull >= 62:
            direction = "BULL"
        elif prob_bear >= 62:
            direction = "BEAR"
        else:
            direction = "NEUTRAL"

        # Confidence level
        spread = abs(prob_bull - prob_bear)
        if spread >= 40:
            confidence = "Strong"
        elif spread >= 20:
            confidence = "Moderate"
        else:
            confidence = "Weak"

        return TimeframeBias(
            tf=timeframe, tf_name=tf_name,
            bull_pct=round(prob_bull, 1),
            bear_pct=round(prob_bear, 1),
            direction=direction,
            confidence=confidence,
            score=round(score, 3),
            signals=signals,
        )
    except Exception as e:
        log.warning("bias analyze error on %s %s: %s", symbol,
                    TF_NAMES.get(timeframe, timeframe), e)
        return None


def analyze_all_timeframes(symbol: str,
                           lookback: int = 100) -> Dict[str, TimeframeBias]:
    """
    Run analyze_bias on all 5 timeframes (M1→H1).
    Returns dict keyed by TF name e.g. {"M1": ..., "M5": ..., ...}
    """
    result = {}
    for tf in BIAS_TIMEFRAMES:
        name = TF_NAMES[tf]
        bias = analyze_bias(symbol, tf, lookback)
        if bias:
            result[name] = bias
    return result
