"""
backtest/run_backtest.py — Command-line entry point.

TWO-STEP WORKFLOW (recommended):

  Step 1 - ON THE WINDOWS MACHINE with MT5 terminal running, export
  real historical data to a file once:

      python -m backtest.run_backtest export \
          --symbol EURUSD --from 2026-06-01 --to 2026-06-02 \
          --out eurusd_june1.json

  Step 2 - run the backtest itself, as many times as you want, with
  different settings, WITHOUT needing MT5 running (this is what lets
  you select any symbol/date and iterate quickly):

      python -m backtest.run_backtest run \
          --data-file eurusd_june1.json --mode 1 --balance 100 \
          --zones-auto-fvg --min-gap-pips 3

ONE-STEP (if you're already running this script ON the machine with
MT5 open, you can skip the export and pull+run in a single command):

      python -m backtest.run_backtest run \
          --symbol EURUSD --from 2026-06-01 --to 2026-06-02 \
          --mode 1 --balance 100 --zones-auto-fvg

Entries are manual in this bot — for a backtest that reflects your
OWN historical decisions rather than an auto-detected proxy, supply
--zones-csv path/to/your_rectangles.csv (columns: time,top,bottom).
"""
import argparse
import datetime as dt
import sys


def _parse_date(s: str) -> dt.datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Unrecognized date format: {s}")


def cmd_export(args):
    from backtest import data_loader
    data_loader.export_to_file(
        symbol=args.symbol, date_from=_parse_date(args.date_from),
        date_to=_parse_date(args.date_to), out_path=args.out,
        prefer_ticks=not args.bars_only, spread_points=args.spread_points,
    )


def cmd_run(args):
    if args.data_file:
        from backtest import data_loader
        spec, series, source = data_loader.load_from_file(args.data_file)
        symbol = spec.symbol
        print(f"Loaded {len(series)} {source} points for {symbol} from {args.data_file}")
    else:
        if not (args.symbol and args.date_from and args.date_to):
            sys.exit("Either --data-file, or all of --symbol/--from/--to, are required.")
        from backtest import data_loader
        data_loader.connect()
        spec = data_loader.get_symbol_spec(args.symbol)
        series, source = data_loader.load_history(
            args.symbol, _parse_date(args.date_from), _parse_date(args.date_to),
            prefer_ticks=not args.bars_only, spread_points=args.spread_points,
        )
        symbol = args.symbol
        print(f"Pulled {len(series)} {source} points for {symbol} live from MT5")

    if not series:
        sys.exit("No price data — nothing to backtest.")

    from backtest import zone_generator
    if args.zones_csv:
        zones = zone_generator.from_csv(args.zones_csv)
        print(f"Loaded {len(zones)} manual rectangle(s) from {args.zones_csv}")
    elif args.zones_auto_fvg:
        bars = _resample_to_m1(series)
        pip = _pip_size_from_spec(spec)
        zones = zone_generator.from_fvg_autodetect(bars, pip, min_gap_pips=args.min_gap_pips)
        zones = zone_generator.dedupe_overlapping(zones)
        print(f"Auto-detected {len(zones)} FVG-based rectangle(s) "
              f"(min_gap_pips={args.min_gap_pips})")
    else:
        sys.exit("Specify either --zones-csv or --zones-auto-fvg.")

    if not zones:
        sys.exit("No entry rectangles to trade — nothing to backtest.")

    from backtest.engine import BacktestConfig, run_backtest
    cfg = BacktestConfig(
        symbol=symbol, start_balance=args.balance, soft_lot_mode=args.mode,
        loss_free_enabled=not args.no_loss_free, risk_free_enabled=not args.no_risk_free,
        base_lot=args.base_lot,
    )
    result = run_backtest(cfg, spec, series, zones)

    from backtest import report
    report.print_summary(result)
    if args.show_events:
        report.print_events(result, limit=args.show_events)
    if args.export_csv:
        report.export_csv(result, args.export_csv)
        print(f"Events exported to {args.export_csv}")
    if args.export_equity_csv:
        report.export_equity_curve_csv(result, args.export_equity_csv)
        print(f"Equity curve exported to {args.export_equity_csv}")
    if args.export_json:
        report.export_json(result, args.export_json)
        print(f"Full result exported to {args.export_json}")
    if args.plot:
        report.plot_equity_curve(result, args.plot)


def _pip_size_from_spec(spec) -> float:
    if spec.digits in (0, 1):
        return 1.0
    return spec.point * 10


def _resample_to_m1(series):
    """series: list of (ts, bid, ask). Groups into 60-second buckets
    and emits OHLC bars from the bid side, for FVG zone detection only."""
    buckets = {}
    for ts, bid, ask in series:
        key = int(ts // 60) * 60
        b = buckets.setdefault(key, {"time": key, "open": bid, "high": bid, "low": bid, "close": bid})
        b["high"] = max(b["high"], bid)
        b["low"] = min(b["low"], bid)
        b["close"] = bid
    return [buckets[k] for k in sorted(buckets.keys())]


def main():
    p = argparse.ArgumentParser(description="TraderBot v4 backtest")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="Pull real MT5 data to a file (run on the MT5 machine)")
    pe.add_argument("--symbol", required=True)
    pe.add_argument("--from", dest="date_from", required=True)
    pe.add_argument("--to", dest="date_to", required=True)
    pe.add_argument("--out", required=True)
    pe.add_argument("--bars-only", action="store_true", help="Skip tick data, use M1 bars")
    pe.add_argument("--spread-points", type=float, default=None)
    pe.set_defaults(func=cmd_export)

    pr = sub.add_parser("run", help="Run a backtest")
    pr.add_argument("--data-file", help="Pre-exported JSON from the 'export' command")
    pr.add_argument("--symbol", help="Pull live from MT5 instead of --data-file")
    pr.add_argument("--from", dest="date_from")
    pr.add_argument("--to", dest="date_to")
    pr.add_argument("--bars-only", action="store_true")
    pr.add_argument("--spread-points", type=float, default=None)
    pr.add_argument("--zones-csv", help="Manual rectangles: time,top,bottom[,label]")
    pr.add_argument("--zones-auto-fvg", action="store_true", help="Auto-generate from FVG detection")
    pr.add_argument("--min-gap-pips", type=float, default=3.0)
    pr.add_argument("--mode", type=int, choices=[1, 2, 3], default=1, help="Soft lot mode")
    pr.add_argument("--balance", type=float, default=100.0)
    pr.add_argument("--base-lot", type=float, default=0.01)
    pr.add_argument("--no-loss-free", action="store_true")
    pr.add_argument("--no-risk-free", action="store_true")
    pr.add_argument("--show-events", type=int, default=0, metavar="N")
    pr.add_argument("--export-csv")
    pr.add_argument("--export-equity-csv")
    pr.add_argument("--export-json")
    pr.add_argument("--plot", metavar="PNG_PATH")
    pr.set_defaults(func=cmd_run)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()