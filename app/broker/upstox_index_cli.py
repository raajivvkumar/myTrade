"""Read-only Upstox Basic/Analytics Token 1-minute index data importer."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from app.broker.upstox_index_history import UpstoxIndexClient


def parse_date(s):
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD date format") from exc


def make_windows(start: date, end: date):
    """28 inclusive calendar-day windows, without overlaps or future lookahead."""
    if start > end or start < date(2022, 1, 1):
        raise ValueError("Requires start >= 2022-01-01 and end >= start")
    current = start
    while current <= end:
        until = min(end, current + timedelta(days=27))
        yield current, until
        current = until + timedelta(days=1)


def make_path(root: Path, symbol: str, start: date,
              end: date, minutes: int) -> Path:
    return root / symbol / f"{minutes}minute" / f"{start}_to_{end}_inclusive.parquet"


def build_parser():
    parser = argparse.ArgumentParser(
        description="MyTrade: Upstox free read-only NIFTY index history"
    )
    parser.add_argument("--from-date", required=True, type=parse_date)
    parser.add_argument("--to-date", required=True, type=parse_date, help="Inclusive")
    parser.add_argument("--index", choices=["NIFTY"], default="NIFTY")
    parser.add_argument("--minutes", type=int, choices=range(1, 16),
                        default=1, metavar="1..15")
    parser.add_argument("--output-dir")
    parser.add_argument("--execute", action="store_true",
                        help="Make read-only Upstox historical requests")
    parser.add_argument("--max-windows", type=int, default=1,
                        help="Safety limit for executed requests; default=1")
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    parser.add_argument("--backup-dir", type=Path,
                        default=Path("../MyTradeOfflineArchive/upstox_index_v3"),
                        help="Automatic offline mirror for each download")
    parser.add_argument("--skip-backup", action="store_true",
                        help="Disable automatic secondary local copy")
    return parser


def run(args):
    if args.max_windows < 1 or args.pause_seconds < 0:
        raise ValueError("max-windows must be positive; pause-seconds nonnegative")
    windows = list(make_windows(args.from_date, args.to_date))
    root = Path(args.output_dir or Path(os.getenv("MYTRADE_DATA_DIR", "data"))
                / "raw" / "upstox" / "index_unvalidated")
    print("READ-ONLY DOWNLOAD" if args.execute else "DRY RUN (no credentials or network)")
    print(f"Provider: Upstox Historical Candle V3; index: {args.index}; "
          f"interval: {args.minutes} minute(s)")
    print(f"Windows: {len(windows)}; output remains UNVALIDATED")
    for start, end in windows:
        print(f"  {start}..{end} -> {make_path(root, args.index, start, end, args.minutes)}")
    if not args.execute:
        return
    if len(windows) > args.max_windows:
        raise ValueError(
            f"Request spans {len(windows)} windows, but max-windows={args.max_windows}. "
            "Explicitly increase the limit only after a small sample is verified."
        )
    client = UpstoxIndexClient()
    for i, (start, end) in enumerate(windows):
        target = make_path(root, args.index, start, end, args.minutes)
        if target.exists():
            print(f"SKIP already captured: {target}")
            continue
        if i:
            time.sleep(args.pause_seconds)
        frame = client.history(index=args.index, start=start,
                               end=end, minutes=args.minutes)
        if frame.empty:
            print(f"No candles in {start}..{end}; nothing saved")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(target)
        frame.to_parquet(target, index=False)
        manifest = {
            "source": "Upstox Historical Candle Data V3",
            "index": args.index, "instrument_key": frame.instrument_key.iloc[0],
            "from_date": str(start), "to_date_inclusive": str(end),
            "interval_minutes": args.minutes, "rows": len(frame),
            "first_timestamp_ist": str(frame.timestamp.min()),
            "last_timestamp_ist": str(frame.timestamp.max()),
            "quality": "UNVALIDATED_NEEDS_INDEPENDENT_NSE_CHECK",
            "data_kind": "INDEX_CANDLES_NOT_OPTION_CONTRACT",
            "warning": "Index volume/OI are not individual CE/PE option volume/OI",
        }
        target.with_suffix(".json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Saved {len(frame)} candles: {target}")
        if not getattr(args, "skip_backup", False):
            from app.data.market_archive import backup_local
            mirrored = backup_local(root, args.backup_dir)
            print(f"Offline mirror verified: {mirrored['pairs']} files at {args.backup_dir}")


def main():
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
