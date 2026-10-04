"""myTrade command-line entry point."""

from __future__ import annotations

import argparse
import json
import os

import pandas as pd
from datetime import datetime
from pathlib import Path

from app.backtest.engine import BacktestConfig, run_backtest
from app.broker.angel_auth import connect_market_data
from app.broker.angel_historical import get_candles
from app.broker.instruments import load_instruments, search_instruments
from app.config import Settings
from app.data.storage import load_candles_parquet, save_candles_parquet
from app.strategy.ema_crossover import generate_ema_crossover_signals


def parse_datetime(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use date/time format: YYYY-MM-DD HH:MM"
        ) from exc


def cmd_status(settings: Settings) -> None:
    settings.ensure_local_directories()
    print("myTrade configuration")
    print(f"  data directory: {settings.data_dir}")

    angel_variables = (
        "ANGEL_API_KEY",
        "ANGEL_CLIENT_CODE",
        "ANGEL_PIN",
        "ANGEL_TOTP_SECRET",
    )
    configured = sum(bool(os.getenv(name)) for name in angel_variables)
    print(f"  Angel One local credentials: {configured}/{len(angel_variables)} configured")
    print("  Angel public instrument master: ready")
    print("  Angel historical candles: ready after local authentication")
    print("  Deterministic backtesting engine: ready")


def cmd_angel_login() -> None:
    session = connect_market_data()
    print("Angel One market-data authentication successful.")
    print("Session/feed tokens received in memory; values are intentionally hidden.")


def cmd_find_instrument(settings: Settings, args: argparse.Namespace) -> None:
    settings.ensure_local_directories()
    cache_path = settings.data_dir / "reference" / "angel_instruments.json"
    instruments = load_instruments(cache_path, force_refresh=args.refresh)
    matches = search_instruments(
        instruments,
        args.query,
        exchange=args.exchange,
        limit=args.limit,
    )

    if not matches:
        print("No matching instruments found.")
        return

    for item in matches:
        print(
            json.dumps(
                {
                    "token": item.get("token"),
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "exchange": item.get("exch_seg"),
                    "instrument_type": item.get("instrumenttype"),
                    "expiry": item.get("expiry"),
                    "lot_size": item.get("lotsize"),
                },
                ensure_ascii=False,
            )
        )


def cmd_download(settings: Settings, args: argparse.Namespace) -> None:
    settings.ensure_local_directories()
    session = connect_market_data()

    frame = get_candles(
        session.client,
        exchange=args.exchange,
        symbol_token=args.token,
        interval=args.interval,
        from_datetime=args.from_dt,
        to_datetime=args.to_dt,
    )
    if frame.empty:
        print("Angel One returned no candles for this request.")
        return

    output = Path(args.output) if args.output else (
        settings.data_dir
        / "raw"
        / args.exchange.upper()
        / str(args.token)
        / f"{args.interval.upper()}.parquet"
    )
    save_candles_parquet(frame, output)

    print(f"Saved {len(frame)} candles to {output}")
    print(frame.tail(5).to_string(index=False))


def cmd_backtest(settings: Settings, args: argparse.Namespace) -> None:
    settings.ensure_local_directories()
    input_path = Path(args.input)
    if input_path.suffix.lower() == ".csv":
        frame = pd.read_csv(input_path)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = (
            frame.dropna(subset=["timestamp", "open", "close"])
            .drop_duplicates(subset=["timestamp"], keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
    else:
        frame = load_candles_parquet(input_path)

    strategy_frame = generate_ema_crossover_signals(
        frame,
        fast_period=args.fast,
        slow_period=args.slow,
        allow_short=args.allow_short,
    )

    config = BacktestConfig(
        initial_capital=args.capital,
        quantity=args.quantity,
        fee_per_order=args.fee_per_order,
        slippage_bps=args.slippage_bps,
        allow_short=args.allow_short,
    )
    result = run_backtest(frame, strategy_frame["signal"], config)

    print("myTrade backtest result")
    print(f"  input: {input_path}")
    print(f"  strategy: EMA crossover {args.fast}/{args.slow}")
    print(f"  execution: signal on close -> next candle open")
    print(json.dumps(result.metrics, indent=2))

    output_dir = Path(args.output_dir) if args.output_dir else (
        settings.data_dir
        / "backtests"
        / f"{input_path.stem}_ema_{args.fast}_{args.slow}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    result.trades_frame().to_csv(output_dir / "trades.csv", index=False)
    result.equity_curve.to_csv(output_dir / "equity_curve.csv", index=False)
    strategy_frame[["timestamp", "close", "ema_fast", "ema_slow", "signal"]].to_csv(
        output_dir / "signals.csv",
        index=False,
    )
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(result.metrics, handle, indent=2)

    print(f"Backtest artifacts saved to {output_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myTrade",
        description="Local-first Indian market research and prediction engine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "status",
        help="Show local project readiness without exposing secret values",
    )
    subparsers.add_parser(
        "angel-login",
        help="Verify local Angel One market-data authentication",
    )

    find_parser = subparsers.add_parser(
        "find-instrument",
        help="Search Angel One's public instrument master",
    )
    find_parser.add_argument("query")
    find_parser.add_argument("--exchange", default=None)
    find_parser.add_argument("--limit", type=int, default=20)
    find_parser.add_argument("--refresh", action="store_true")

    download_parser = subparsers.add_parser(
        "download",
        help="Download historical candles from Angel One and save as Parquet",
    )
    download_parser.add_argument("--exchange", required=True, help="Example: NSE or NFO")
    download_parser.add_argument("--token", required=True, help="Angel symbol token")
    download_parser.add_argument("--interval", default="FIVE_MINUTE")
    download_parser.add_argument("--from", dest="from_dt", required=True, type=parse_datetime)
    download_parser.add_argument("--to", dest="to_dt", required=True, type=parse_datetime)
    download_parser.add_argument("--output", default=None)

    backtest_parser = subparsers.add_parser(
        "backtest",
        help="Backtest a deterministic strategy against locally cached candle data",
    )
    backtest_parser.add_argument("--input", required=True, help="Path to a candle Parquet or OHLC CSV file")
    backtest_parser.add_argument("--fast", type=int, default=9, help="Fast EMA period")
    backtest_parser.add_argument("--slow", type=int, default=21, help="Slow EMA period")
    backtest_parser.add_argument("--capital", type=float, default=100000.0)
    backtest_parser.add_argument("--quantity", type=float, default=1.0)
    backtest_parser.add_argument("--fee-per-order", type=float, default=0.0)
    backtest_parser.add_argument("--slippage-bps", type=float, default=0.0)
    backtest_parser.add_argument("--allow-short", action="store_true")
    backtest_parser.add_argument("--output-dir", default=None)

    return parser


def main() -> None:
    settings = Settings.from_env()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "status":
        cmd_status(settings)
    elif args.command == "angel-login":
        cmd_angel_login()
    elif args.command == "find-instrument":
        cmd_find_instrument(settings, args)
    elif args.command == "download":
        cmd_download(settings, args)
    elif args.command == "backtest":
        cmd_backtest(settings, args)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
