"""
backtest/report.py — Human-readable summary + CSV/JSON export of a
BacktestResult.
"""
import csv
import json


def print_summary(result):
    r = result
    print("=" * 64)
    print(f"  BACKTEST RESULT — {r.symbol}")
    print("=" * 64)
    print(f"  Start balance     : ${r.start_balance:,.2f}")
    print(f"  Final balance     : ${r.final_balance:,.2f}")
    print(f"  Final equity      : ${r.final_equity:,.2f}")
    print(f"  Total return      : {r.total_return_pct:+.2f}%")
    print(f"  Max drawdown      : {r.max_drawdown_pct:.2f}%")
    print(f"  Zones traded      : {r.zones_traded}")
    print(f"  Wins / Losses     : {r.wins} / {r.losses}"
          + (f"  (win rate {100*r.wins/(r.wins+r.losses):.1f}%)" if (r.wins + r.losses) else ""))
    print(f"  Kill switch       : {'TRIPPED — ' + r.kill_switch_reason if r.kill_switch_tripped else 'not tripped'}")
    print("-" * 64)
    print(f"  {len(r.deals)} closed deal(s), {len(r.events)} total event(s)")
    print("=" * 64)


def print_events(result, limit=50):
    print(f"\nLast {limit} events:")
    for e in result.events[-limit:]:
        kind = e.get("kind")
        if kind == "FILL":
            print(f"  FILL        #{e['ticket']:<6} {e['side']:<4} @ {e['price']:.5f} lot={e['lot']:.2f}")
        elif kind == "FILL_MARKET":
            print(f"  FILL(MKT)   #{e['ticket']:<6} {e['side']:<4} @ {e['price']:.5f} lot={e['lot']:.2f}")
        elif kind == "PARTIAL_CLOSE":
            print(f"  PARTIAL     #{e['ticket']:<6} closed={e['closed_lot']:.2f} "
                  f"remain={e['remaining_lot']:.2f} profit=${e['profit']:+.2f} bal=${e['balance']:.2f}")
        elif kind == "CLOSE":
            print(f"  CLOSE({e['reason']:<3}) #{e['ticket']:<6} {e['side']:<4} @ {e['price']:.5f} "
                  f"profit=${e['profit']:+.2f} bal=${e['balance']:.2f}")


def export_csv(result, path: str):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "kind", "ticket", "side", "price", "lot_or_closed",
                    "remaining", "profit", "balance", "reason"])
        for e in result.events:
            w.writerow([
                e.get("t"), e.get("kind"), e.get("ticket"), e.get("side", ""),
                e.get("price", ""), e.get("lot", e.get("closed_lot", "")),
                e.get("remaining_lot", ""), e.get("profit", ""),
                e.get("balance", ""), e.get("reason", ""),
            ])


def export_equity_curve_csv(result, path: str):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "equity", "balance"])
        for ts, eq, bal in result.equity_curve:
            w.writerow([ts, round(eq, 2), round(bal, 2)])


def export_json(result, path: str):
    payload = {k: v for k, v in vars(result).items()}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def plot_equity_curve(result, out_path: str = "equity_curve.png"):
    """Optional — requires matplotlib. Skips silently if not installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import datetime as dt
    except ImportError:
        print("matplotlib not installed — skipping equity curve plot "
              "(pip install matplotlib to enable)")
        return None

    times = [dt.datetime.fromtimestamp(t) for t, _, _ in result.equity_curve]
    equity = [e for _, e, _ in result.equity_curve]
    balance = [b for _, _, b in result.equity_curve]

    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(times, equity, label="Equity", color="#00A896", linewidth=1.2)
    ax.plot(times, balance, label="Balance", color="#1B4965", linewidth=1.0, linestyle="--")
    ax.axhline(result.start_balance, color="#888", linewidth=0.7, linestyle=":")
    ax.set_title(f"{result.symbol} — Backtest Equity Curve")
    ax.set_ylabel("USD")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    print(f"Equity curve saved to {out_path}")
    return out_path