"""Safe CLI probe for Free Upstox NIFTY history; explicit --execute only.

Examples:
  python -m app.broker.upstox_basic_history_cli --mode index --date 2026-10-07
  python -m app.broker.upstox_basic_history_cli --mode index --date 2026-10-07 --execute
  python -m app.broker.upstox_basic_history_cli --mode option --expiry current_month --strike 25000 --side PE --date 2026-10-07 --execute

NO trading orders, NO Plus-only expired endpoints, NO automatic disk archive.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.broker.upstox_basic_history import (
    INDEX_KEYS, candle_quality, fetch_day_1m, resolve_active_option,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upstox Free/Analytics Token: NIFTY 1-minute read-only availability probe"
    )
    parser.add_argument("--mode", choices=["index", "vix", "option"], default="index")
    parser.add_argument("--date", required=True, help="Trading day YYYY-MM-DD")
    parser.add_argument("--expiry", default="current_week", help="Option relative expiry or YYYY-MM-DD")
    parser.add_argument("--strike", type=float, help="Exact NIFTY option strike; option mode only")
    parser.add_argument("--side", choices=["CE", "PE"], help="Option type; option mode only")
    parser.add_argument(
        "--execute", action="store_true", help="Permit read-only network GET(s); otherwise dry run"
    )
    return parser


def run(args: argparse.Namespace, *, session=None, token: str | None = None) -> dict:
    try:
        if date.fromisoformat(args.date).isoformat() != args.date:
            raise ValueError()
    except ValueError as exc:
        raise ValueError("--date must be YYYY-MM-DD") from exc
    if date.fromisoformat(args.date) > datetime.now(ZoneInfo("Asia/Kolkata")).date():
        raise ValueError("Future trading dates cannot have observed candles")
    if args.mode == "option" and (
        args.strike is None or args.side not in ("CE", "PE")
    ):
        raise ValueError("Option mode requires both --strike and --side")
    if args.mode != "option" and (args.strike is not None or args.side is not None):
        raise ValueError("--strike / --side are allowed only for --mode option")
    if not args.execute:
        print("DRY RUN: 0 API requests, no market-data files or trades.")
        print(f"Would check {args.mode} 1-minute candles for {args.date}.")
        if args.mode == "option":
            print(f"Would resolve ACTIVE contract: {args.expiry} {args.strike:g} {args.side}.")
        return {"status": "DRY_RUN", "requests": 0}

    identity = None
    if args.mode == "option":
        identity = resolve_active_option(
            args.expiry, args.strike, args.side, token=token, session=session
        )
        if args.date > identity["expiry"]:
            raise ValueError("Date after actual option expiry; refusing expired-option query")
        key = identity["instrument_key"]
    else:
        key = INDEX_KEYS[args.mode]

    bars = fetch_day_1m(key, args.date, token=token, session=session)
    quality = candle_quality(bars)
    print(f"Upstox {args.mode} read-only 1-minute data: {args.date}")
    print(f"Actual instrument key: {key}")
    if identity is not None:
        print(
            f"Exact contract: expiry={identity['expiry']} "
            f"strike={identity['strike_price']:g} {identity['option_type']}"
        )
    print(
        f"Bars={quality['observed_candles']}; "
        f"regular-session minutes={quality['regular_session_minutes']}/375; "
        f"missing={quality['missing_regular_minutes']}"
    )
    if bars:
        print(f"First={quality['first']}; last={quality['last']}; last close={bars[-1]['close']}")
    else:
        print("NO DATA: This contract/day is not available via this Basic endpoint.")
    if not quality["research_ready_contiguous_session"]:
        print("RESEARCH GATE: incomplete 1-minute day; do NOT fill gaps or claim 3x events.")
    print(
        "No historical Greeks were received from this OHLC endpoint. "
        "Gamma 3x/5x/10x prediction is NOT validated. Nothing saved to disk."
    )
    return {"status": "OBSERVED" if bars else "EMPTY", "mode": args.mode,
            "instrument_key": key, "contract": identity, "quality": quality,
            "requests": 2 if identity else 1}


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
