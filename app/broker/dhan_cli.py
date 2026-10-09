"""Read-only DhanHQ CLI. Defaults to dry-run (no network or credentials)."""
from __future__ import annotations
import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from app.broker.dhan_historical import DhanClient, RollingQuery, windows
from app.broker.dhan_probe import run_nifty_probe


def load_local_credentials():
    """Load only this checkout's .env; process environment takes precedence."""
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


def parser():
    p = argparse.ArgumentParser(description="MyTrade NIFTY read-only DhanHQ Data API")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Verify token and Data API status without showing credentials")
    n = commands.add_parser("nifty", help="Preview / test 09:15-09:30 IST NIFTY 1-minute candles")
    n.add_argument("--date", type=date.fromisoformat, required=True)
    n.add_argument("--execute", action="store_true", help="Enable profile and one historical data request")
    r = commands.add_parser("rolling", help="Preview / collect 1-minute rolling expired-option data")
    r.add_argument("--from-date", type=date.fromisoformat, required=True)
    r.add_argument("--to-date", type=date.fromisoformat, required=True, help="Exclusive end date")
    r.add_argument("--expiry-flag", choices=("WEEK", "MONTH"), default="MONTH")
    r.add_argument("--expiry-code", choices=(1, 2, 3), type=int, default=1,
                   help="Expired rolling options: 1=near, 2=next, 3=far")
    r.add_argument("--strike", default="ATM")
    r.add_argument("--side", choices=("CALL", "PUT"), default="PUT")
    r.add_argument("--interval", choices=(1, 5, 15, 25, 60), type=int, default=1)
    r.add_argument("--output-dir", default=None)
    r.add_argument("--pause-seconds", type=float, default=1)
    r.add_argument("--execute", action="store_true", help="Enable paid data requests (never trades)")
    return p


def path_for(root: Path, query: RollingQuery) -> Path:
    return (root / "NIFTY" / f"{query.expiry_flag}_{query.expiry_code}"
            / query.strike / query.side / f"{query.interval}m"
            / f"{query.start}_to_{query.end}_exclusive.parquet")


def run_rolling(args):
    if args.pause_seconds < 0:
        raise ValueError("Delay must be nonnegative")
    root = Path(args.output_dir or Path(os.getenv("MYTRADE_DATA_DIR", "data"))
                / "raw" / "dhan" / "rolling")
    queries = [RollingQuery(a, b, expiry_flag=args.expiry_flag, expiry_code=args.expiry_code,
                            strike=args.strike.upper(), side=args.side, interval=args.interval)
               for a, b in windows(args.from_date, args.to_date)]
    print("READ-ONLY DATA COLLECTION" if args.execute else "DRY RUN (no network access)")
    print("WARNING: ATM relative series may switch actual strike; NOT fixed contracts.")
    for query in queries:
        print(f"{query.start} to {query.end} exclusive: {path_for(root, query)}")
    if not args.execute:
        return

    load_local_credentials()
    client = DhanClient()
    if str(client.profile().get("dataPlan", "")).lower() != "active":
        raise RuntimeError("Dhan Data API plan not active; no historical calls made")
    for index, query in enumerate(queries):
        dest = path_for(root, query)
        if dest.exists():
            print(f"SKIP (cached): {dest}")
            continue
        if index:
            time.sleep(args.pause_seconds)
        frame = client.rolling(query)
        if frame.empty:
            print(f"EMPTY: {query.start} to {query.end}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(dest, index=False)
        manifest = {
            "provider": "DhanHQ",
            "endpoint": "/v2/charts/rollingoption",
            "request": query.payload(),
            "row_count": len(frame),
            "first_timestamp_ist": str(frame.timestamp.min()),
            "last_timestamp_ist": str(frame.timestamp.max()),
            "actual_strike_changes": int(frame.actual_strike.ne(frame.actual_strike.shift()).sum() - 1),
            "validation_status": "UNVALIDATED",
            "contract_identity": "ROLLING_RELATIVE_NOT_FIXED",
            "warning": "Do not measure fixed-contract multipliers across actual strike changes",
        }
        dest.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"SAVED {len(frame):,} rows: {dest}")


def main():
    args = parser().parse_args()
    try:
        if args.command == "status":
            load_local_credentials()
            plan = DhanClient().profile()
            print("Data API plan: " + str(plan.get("dataPlan") or "Unknown"))
            print("Data validity: " + str(plan.get("dataValidity") or "Unknown"))
            print("Token validity: " + str(plan.get("tokenValidity") or "Unknown"))
        elif args.command == "nifty":
            if args.execute:
                load_local_credentials()
            print(json.dumps(run_nifty_probe(args.date, execute=args.execute), indent=2))
        else:
            run_rolling(args)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
