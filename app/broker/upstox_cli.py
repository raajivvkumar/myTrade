"""MyTrade read-only Upstox CLI; dry-run is always the default."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from datetime import date, timedelta
from pathlib import Path
from app.broker.upstox_historical import UpstoxClient, find_contract


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD") from exc


def parser():
    p = argparse.ArgumentParser(description="NIFTY expired option read-only data importer")
    sub = p.add_subparsers(dest="action", required=True)
    x = sub.add_parser("status")
    x.add_argument("--execute", action="store_true")
    x = sub.add_parser("expiries")
    x.add_argument("--execute", action="store_true")
    x = sub.add_parser("contracts")
    x.add_argument("--expiry", required=True, type=parse_date)
    x.add_argument("--limit", type=int, default=25)
    x.add_argument("--execute", action="store_true")
    x = sub.add_parser("probe")
    x.add_argument("--expiry", required=True, type=parse_date)
    x.add_argument("--strike", required=True, type=float)
    x.add_argument("--side", required=True, choices=("CE", "PE"))
    x.add_argument("--from-date", type=parse_date, default=None)
    x.add_argument("--to-date", type=parse_date, default=None, help="Inclusive end")
    x.add_argument("--interval", choices=("1minute", "3minute", "5minute",
                                            "15minute", "30minute", "day"), default="1minute")
    x.add_argument("--output-dir", default=None)
    x.add_argument("--execute", action="store_true")
    return p


def output_for(root, contract, start, end, interval):
    digest = hashlib.sha256(contract["instrument_key"].encode()).hexdigest()[:16]
    return (root / "NIFTY" / contract["expiry"]
            / f"{contract['strike_price']:g}{contract['option_type']}_{digest}"
            / f"{interval}_{start}_{end}.parquet")


def run(args):
    if args.action in ("status", "expiries", "contracts") and not args.execute:
        print("DRY RUN: no network, no trading, no subscription required.")
        return
    if args.action == "status":
        print("Active Upstox profile:", UpstoxClient().profile_active())
        print("NOTE: profile activation does NOT prove expired options/Plus access")
        return
    if args.action == "expiries":
        results = UpstoxClient().expiries()
        print(f"Provider returned {len(results)} NIFTY expired dates:")
        for expiry in results:
            print(expiry)
        return
    if args.action == "contracts":
        if args.limit < 1:
            raise ValueError("Limit must be positive")
        contracts = UpstoxClient().contracts(args.expiry)
        print(f"Contracts: {len(contracts)}; showing first {args.limit}")
        for item in contracts[:args.limit]:
            print(f"{item['strike_price']:g} {item['option_type']} lot={item['lot_size']}")
        return

    start = args.from_date or args.expiry - timedelta(days=9)
    end = args.to_date or args.expiry
    if not (start <= end <= args.expiry) or not math.isfinite(args.strike) or args.strike <= 0:
        raise ValueError("From/to/expiry and strike must be valid")
    print("READ-ONLY API PROBE" if args.execute else "DRY RUN: no network, no subscription")
    print(f"NIFTY {args.expiry} {args.strike:g}{args.side}, {args.interval}, {start}..{end} inclusive")
    if not args.execute:
        return
    client = UpstoxClient()
    contract = find_contract(client.contracts(args.expiry), args.expiry, args.strike, args.side)
    root = Path(args.output_dir or Path(os.getenv("MYTRADE_DATA_DIR", "data"))
                / "raw" / "upstox" / "unvalidated")
    target = output_for(root, contract, start, end, args.interval)
    if target.exists():
        print(f"Already cached, original preserved: {target}")
        return
    frame = client.candles(contract, start, end, args.interval)
    if frame.empty:
        print("No candles returned. Nothing saved.")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    frame.to_parquet(target, index=False)
    manifest = {
        "provider": "Upstox", "endpoint": "/v2/expired-instruments",
        "contract": contract, "interval": args.interval,
        "start": str(start), "end_inclusive": str(end), "rows": len(frame),
        "first_ist": str(frame.timestamp.min()), "last_ist": str(frame.timestamp.max()),
        "quality": "UNVALIDATED_NEEDS_NSE_CROSSCHECK",
        "warning": "Broker-sourced prices not independently NSE-verified; paper research only",
    }
    target.with_suffix(".json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Saved {len(frame)} unvalidated candles to {target}")


def main():
    run(parser().parse_args())


if __name__ == "__main__":
    main()
