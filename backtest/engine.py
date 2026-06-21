"""
backtest/engine.py — Replays real historical price data through the
ACTUAL, unmodified core/position_monitor.SourceState logic.

IMPORTANT: fake_mt5.install() must run BEFORE this module's first
`from core.position_monitor import SourceState` import executes in
this process — Python caches module imports, and `core/position_monitor.py`
does `import MetaTrader5 as mt5` at its own module level. If the real
package (or a different fake) got imported first in this process,
re-importing here will NOT rebind it. run_backtest.py enforces this
ordering; don't import core.position_monitor anywhere else first.
"""
import logging
from dataclasses import dataclass, field

log = logging.getLogger("backtest")


@dataclass
class BacktestConfig:
    symbol: str
    start_balance: float = 100.0
    soft_lot_mode: int = 1
    loss_free_enabled: bool = True
    risk_free_enabled: bool = True
    base_lot: float = 0.01
    magic: int = 998877
    # config.py overrides applied for the duration of the run (restored after)
    config_overrides: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    symbol: str
    start_balance: float
    final_balance: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    zones_traded: int
    wins: int
    losses: int
    kill_switch_tripped: bool
    kill_switch_reason: str
    deals: list
    events: list
    equity_curve: list   # (timestamp, equity, balance)
    zone_outcomes: list  # per-zone summary


def run_backtest(cfg: BacktestConfig, spec, price_series: list, zones: list) -> BacktestResult:
    """
    spec:          backtest.fake_mt5.SymbolSpec (real contract specs)
    price_series:  list of (timestamp, bid, ask), already time-sorted
    zones:         list of backtest.zone_generator.Zone, already time-sorted
    """
    import backtest.fake_mt5 as fake_mt5
    import config as cfg_module

    # Apply temporary config overrides (soft-lot tables, R-triggers, etc.)
    # for the duration of this run, then restore — config.py is a shared
    # module-level singleton, and other code in the SAME process (e.g. a
    # GUI also running live) must not see these changes leak.
    _restore = {}
    overrides = dict(cfg.config_overrides)
    overrides.setdefault("SOFT_LOT_MODE", cfg.soft_lot_mode)
    for k, v in overrides.items():
        _restore[k] = getattr(cfg_module, k, None)
        setattr(cfg_module, k, v)

    broker = fake_mt5.BacktestBroker(spec=spec, start_balance=cfg.start_balance, magic=cfg.magic)
    fake_mt5.install(broker)

    # These imports MUST happen after install() — see module docstring.
    import core.position_monitor as pm_module
    from core.position_monitor import SourceState
    from core.order_manager import get_pip_size

    pip_size = get_pip_size(cfg.symbol)

    # ── Patch position_monitor's real-wall-clock usage to simulated time ──
    # ACTIVATION_GRACE_SEC (a 5-REAL-second post-activation grace period,
    # see _check_legs's `in_grace` guard) is measured via time.time() —
    # correct for live trading, but a 200,000-tick backtest finishes in
    # under a real second, so `now - self._activated_at` would never
    # exceed 5 and `in_grace` would stay True for the ENTIRE backtest,
    # silently freezing every source the instant both legs activate
    # (no further close detection, no recovery orders, nothing — found
    # by noticing a source stayed "active" with stale ticket numbers for
    # an entire 200k-tick run despite the broker correctly closing both
    # positions). Fix: make position_monitor.py's `_time.time()` return
    # the engine's current SIMULATED timestamp instead of the real
    # clock, and `_time.sleep()` a no-op (no reason to actually pause
    # real execution waiting on simulated retries).
    class _SimTime:
        def __init__(self):
            self.now = 0.0
        def time(self):
            return self.now
        def sleep(self, _seconds):
            pass
    sim_time = _SimTime()
    pm_module._time = sim_time

    halted = {"flag": False, "reason": ""}

    def stop_fn():
        halted["flag"] = True
        halted["reason"] = halted["reason"] or "balance TP / kill switch"

    sources = {}   # zone index -> SourceState
    zone_state = {}  # zone index -> "pending" | "registered" | "done"
    next_zone_idx = 0
    zones = sorted(zones, key=lambda z: z.time)

    peak_equity = cfg.start_balance
    max_dd_pct = 0.0

    if not price_series:
        raise ValueError("price_series is empty — nothing to backtest")

    for ts, bid, ask in price_series:
        if halted["flag"]:
            break

        broker.advance(bid, ask, ts)
        sim_time.now = ts

        # Register any zones whose appearance time has arrived
        while next_zone_idx < len(zones) and zones[next_zone_idx].time <= ts:
            z = zones[next_zone_idx]
            name = z.label or f"zone_{next_zone_idx}"
            st = SourceState(
                name=name, rect_top=max(z.top, z.bottom), rect_bottom=min(z.top, z.bottom),
                pip_size=pip_size, symbol=cfg.symbol, base_lot=cfg.base_lot,
                start_balance=cfg.start_balance,
                log_fn=lambda msg, level="INFO", _n=name: log.debug("[%s] %s", _n, msg),
                stop_fn=stop_fn,
                risk_free_enabled=cfg.risk_free_enabled,
                loss_free_enabled=cfg.loss_free_enabled,
                soft_lot_mode=cfg.soft_lot_mode,
            )
            sources[next_zone_idx] = st
            next_zone_idx += 1

        for idx, st in sources.items():
            if st.state == SourceState.IDLE:
                st.check_touch(bid, ask)
            if st.state in (SourceState.PENDING, SourceState.ACTIVE):
                st.check({"BID": bid})

        eq = broker.equity()
        if eq > peak_equity:
            peak_equity = eq
        dd = (peak_equity - eq) / peak_equity * 100.0 if peak_equity > 0 else 0.0
        if dd > max_dd_pct:
            max_dd_pct = dd

    # Restore config.py to whatever it was before this run
    for k, v in _restore.items():
        if v is None:
            try:
                delattr(cfg_module, k)
            except AttributeError:
                pass
        else:
            setattr(cfg_module, k, v)

    final_balance = broker.balance
    final_equity = broker.equity()

    if halted["flag"]:
        # _close_all_and_stop() is shared by THREE different triggers
        # (R3 balance TP, the touch-count kill switch, and the hard
        # equity-floor kill switch) and calls the same stop_fn either
        # way, so the callback alone can't tell us which one fired.
        # Infer it from the direction of the outcome instead — a
        # profitable stop is the TP target, a losing one is one of
        # the two kill switches.
        if final_balance >= cfg.start_balance:
            halted["reason"] = "R3 balance take-profit reached"
        else:
            halted["reason"] = "kill switch (touch-count limit or hard equity floor)"

    # Classify by actual realized profit sign, not close reason — a
    # position can close "via SL" at a risk-free-locked price that is
    # still profitable, so reason alone would misclassify it as a loss.
    wins = sum(1 for d in broker.deals if d.profit > 0)
    losses = sum(1 for d in broker.deals if d.profit < 0)

    zone_outcomes = []
    for idx, st in sources.items():
        zone_outcomes.append({
            "zone": st.name, "final_state": st.state, "rounds": st.round,
            "touch_count": st.touch_count, "cumulative_loss": round(st.cumulative_loss, 2),
        })

    return BacktestResult(
        symbol=cfg.symbol,
        start_balance=cfg.start_balance,
        final_balance=round(final_balance, 2),
        final_equity=round(final_equity, 2),
        total_return_pct=round((final_balance - cfg.start_balance) / cfg.start_balance * 100.0, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        zones_traded=len(sources),
        wins=wins, losses=losses,
        kill_switch_tripped=halted["flag"],
        kill_switch_reason=halted["reason"],
        deals=[vars(d) for d in broker.deals],
        events=broker.events,
        equity_curve=broker.equity_curve,
        zone_outcomes=zone_outcomes,
    )