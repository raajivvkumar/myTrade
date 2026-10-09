"""Newest-first NIFTY research backfill. Preview by default; broker data only."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.broker.dhan_cli import load_local_credentials
from app.broker.dhan_historical import DhanAPIError, DhanClient, RollingQuery, STRIKE_RE

IST = ZoneInfo("Asia/Kolkata")
VERSION = 1


def five_year_start(day):
    try:
        return day.replace(year=day.year - 5)
    except ValueError:
        return day.replace(year=day.year - 5, day=28)


def backward_windows(start, end):
    if start >= end:
        raise ValueError("Start must precede exclusive end")
    while end > start:
        beginning = max(start, end - timedelta(days=30))
        yield beginning, end
        end = beginning


def index_payload(start, end):
    # Midnight boundaries avoid making 09:15 itself a query boundary.
    return dict(securityId="13", exchangeSegment="IDX_I", instrument="INDEX",
                interval="1", oi=False, fromDate=f"{start} 00:00:00",
                toDate=f"{end} 00:00:00")


def make_plan(start, through, strikes, expiry_flag, expiry_code, *, index_only=False):
    if not strikes or len(set(strikes)) != len(strikes):
        raise ValueError("Provide distinct relative strikes")
    for strike in strikes:
        if not STRIKE_RE.fullmatch(strike):
            raise ValueError("Unsupported relative strike")
    entries = []
    for beginning, end in backward_windows(start, through + timedelta(days=1)):
        entries.append(dict(series="INDEX", start=str(beginning), end=str(end),
                            endpoint="/charts/intraday", payload=index_payload(beginning, end)))
        if index_only:
            continue
        for strike in strikes:
            for side in ("CALL", "PUT"):
                query = RollingQuery(beginning, end, expiry_flag=expiry_flag,
                                     expiry_code=expiry_code, strike=strike, side=side)
                entries.append(dict(series=f"{expiry_flag}_{expiry_code}_{strike}_{side}",
                                    start=str(beginning), end=str(end),
                                    endpoint="/charts/rollingoption", payload=query.payload()))
    return entries


def parse_bars(raw, entry):
    rolling = entry["series"] != "INDEX"
    if rolling:
        if not isinstance(raw.get("data"), dict):
            raise ValueError("Missing rolling-option data object")
        source = raw["data"].get("ce" if entry["payload"]["drvOptionType"] == "CALL" else "pe")
        if source is None:
            return pd.DataFrame()
    else:
        source = raw
    if not isinstance(source, dict):
        raise ValueError("Candle source must be an object")
    required = ["timestamp", "open", "high", "low", "close"]
    if rolling:
        required.append("strike")
    if any(not isinstance(source.get(key), list) for key in required):
        raise ValueError("Missing candle arrays")
    size = len(source["timestamp"])
    if any(len(source[key]) != size for key in required):
        raise ValueError("Candle array lengths differ")
    for key in ("volume", "oi", "iv", "spot", "open_interest"):
        if source.get(key) is not None and (not isinstance(source[key], list) or len(source[key]) != size):
            raise ValueError("Optional candle array lengths differ")
    if not size:
        return pd.DataFrame()
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in source["timestamp"]):
        raise ValueError("Invalid epoch timestamp")
    stamps = pd.to_datetime(source["timestamp"], unit="s", utc=True, errors="coerce")
    frame = pd.DataFrame({"timestamp": stamps.tz_convert("Asia/Kolkata").tz_localize(None)})
    for key in ["open", "high", "low", "close", "volume", "oi", "iv", "spot"] + (["strike"] if rolling else []):
        original = source.get(key, source.get("open_interest") if key == "oi" else None)
        if original is not None and any(isinstance(v, bool) for v in original):
            raise ValueError("Boolean candle values")
        frame[key] = pd.to_numeric(original if original is not None else [None] * size, errors="coerce")
        if original is not None:
            nonnull = pd.Series(original).notna()
            if ((~np.isfinite(frame[key])) & nonnull).any():
                raise ValueError("Invalid numeric candle values")
    if frame[["timestamp", "open", "high", "low", "close"]].isna().any().any():
        raise ValueError("Missing timestamp or OHLC")
    positive = ["open", "high", "low", "close"] + (["strike"] if rolling else [])
    if frame[positive].isna().any().any() or (frame[positive] <= 0).any().any():
        raise ValueError("Non-positive or missing OHLC/strike")
    if ((frame.low > frame[["open", "close"]].min(axis=1)) |
        (frame.high < frame[["open", "close"]].max(axis=1)) | (frame.low > frame.high)).any():
        raise ValueError("Invalid OHLC range")
    if (frame[["volume", "oi", "iv"]].dropna(how="all") < 0).any().any():
        raise ValueError("Negative volume, OI or IV")
    if frame.timestamp.duplicated().any() or not frame.timestamp.is_monotonic_increasing:
        raise ValueError("Duplicate or out-of-order timestamps")
    lower, upper = pd.Timestamp(entry["start"]), pd.Timestamp(entry["end"])
    if ((frame.timestamp < lower) | (frame.timestamp >= upper)).any():
        raise ValueError("Provider returned timestamps outside request window")
    if (frame.timestamp.dt.second.ne(0) | frame.timestamp.dt.microsecond.ne(0)).any():
        raise ValueError("Non-minute timestamp")
    if rolling:
        frame.rename(columns={"strike": "actual_strike"}, inplace=True)
    frame["series"] = entry["series"]
    return frame


def daily_facts(frame):
    if frame.empty:
        return []
    records = []
    for day, bars in frame.groupby(frame.timestamp.dt.date, sort=True):
        bars = bars.sort_values("timestamp")
        # Compare both conventions; do not infer bar-label convention from count alone.
        open_labels = set(pd.date_range(f"{day} 09:15", periods=375, freq="min"))
        close_labels = set(pd.date_range(f"{day} 09:16", periods=375, freq="min"))
        stamps = set(bars.timestamp)
        regular = bars[bars.timestamp.isin(open_labels | close_labels)]
        item = dict(date=str(day), series=str(bars.series.iloc[0]), rows=len(bars),
                    open_label_missing=len(open_labels - stamps),
                    close_label_missing=len(close_labels - stamps),
                    out_of_regular_label_union=len(stamps - (open_labels | close_labels)),
                    timestamp_convention="UNVERIFIED",
                    iv_available_rows=int(bars.iv.notna().sum()),
                    oi_available_rows=int(bars.oi.notna().sum()),
                    strike_switches_within_day=(int(bars.actual_strike.ne(bars.actual_strike.shift()).sum()-1)
                                               if "actual_strike" in bars else 0))
        if not regular.empty:
            opening, closing = float(regular.open.iloc[0]), float(regular.close.iloc[-1])
            high, low = float(regular.high.max()), float(regular.low.min())
            item.update(first_open=opening, last_close=closing, observed_high=high,
                        observed_low=low, observed_range_points=high-low,
                        regular_rows=len(regular))
            if item["series"] == "INDEX":
                item["observed_session_change_pct"] = (closing/opening - 1)*100
        records.append(item)
    return records


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def chunk_base(root, entry):
    return root / "chunks" / entry["series"] / f'{entry["start"]}_to_{entry["end"]}'


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_cached(root, entry, retry_empty=False):
    base = chunk_base(root, entry)
    manifest = base.with_suffix(".manifest.json")
    if not manifest.exists():
        return None
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if value.get("version") != VERSION or value.get("entry") != entry:
        return None
    if value.get("status") == "EMPTY" and retry_empty:
        return None
    raw, parquet = base.with_suffix(".raw.json.gz"), base.with_suffix(".parquet")
    if not raw.exists() or file_hash(raw) != value.get("raw_sha256"):
        raise ValueError("Cached raw data failed integrity check; retained for inspection")
    if value.get("rows", 0) and (not parquet.exists() or file_hash(parquet) != value.get("parquet_sha256")):
        raise ValueError("Cached Parquet failed integrity check; retained for inspection")
    return value


def save_chunk(root, entry, raw, frame):
    base = chunk_base(root, entry)
    base.parent.mkdir(parents=True, exist_ok=True)
    raw_path = base.with_suffix(".raw.json.gz")
    temporary = raw_path.with_name(raw_path.name + "." + uuid4().hex + ".tmp")
    try:
        with gzip.open(temporary, "wt", encoding="utf-8") as out:
            json.dump(raw, out, allow_nan=False)
        temporary.replace(raw_path)
    finally:
        temporary.unlink(missing_ok=True)
    parquet_path = base.with_suffix(".parquet")
    if not frame.empty:
        temporary = parquet_path.with_name(parquet_path.name + "." + uuid4().hex + ".tmp")
        try:
            frame.to_parquet(temporary, index=False)
            temporary.replace(parquet_path)
        finally:
            temporary.unlink(missing_ok=True)
    value = dict(version=VERSION, entry=entry, rows=len(frame),
                 status="DATA" if not frame.empty else "EMPTY",
                 raw_sha256=file_hash(raw_path),
                 parquet_sha256=file_hash(parquet_path) if not frame.empty else None,
                 fetched_at_ist=datetime.now(IST).isoformat(),
                 independently_verified=False, daily=daily_facts(frame))
    atomic_json(base.with_suffix(".manifest.json"), value)
    return value


def write_report(root, entries, completed, errors):
    daily = [row for item in completed for row in item["daily"]]
    yearly = []
    if daily:
        table = pd.DataFrame(daily).sort_values(["series", "date"])
        table["year"] = table.date.str[:4]
        for (series, year), group in table.groupby(["series", "year"], sort=True):
            item = dict(series=series, year=int(year), observed_dates=len(group),
                        rows=int(group.rows.sum()),
                        open_label_missing_total=int(group.open_label_missing.sum()),
                        close_label_missing_total=int(group.close_label_missing.sum()),
                        iv_available_rows=int(group.iv_available_rows.sum()),
                        oi_available_rows=int(group.oi_available_rows.sum()),
                        strike_switches_within_days=int(group.strike_switches_within_day.sum()))
            good = group.dropna(subset=["first_open", "last_close"]) if "first_open" in group else pd.DataFrame()
            if series == "INDEX" and not good.empty:
                item.update(first_observed_date=str(good.date.iloc[0]), last_observed_date=str(good.date.iloc[-1]),
                            first_observed_open=float(good.first_open.iloc[0]),
                            last_observed_close=float(good.last_close.iloc[-1]),
                            observed_span_change_pct=float((good.last_close.iloc[-1]/good.first_open.iloc[0]-1)*100),
                            median_observed_session_range_points=float(good.observed_range_points.median()))
            yearly.append(item)
        table.to_csv(root / "daily_quality.csv", index=False)
    summary = dict(provider="DhanHQ", request_order="NEWEST_FIRST",
                   requested_from=min(e["start"] for e in entries),
                   requested_through=str(date.fromisoformat(max(e["end"] for e in entries))-timedelta(days=1)),
                   planned_chunks=len(entries), completed_chunks=len(completed),
                   empty_chunks=sum(item["status"] == "EMPTY" for item in completed),
                   rows=sum(item["rows"] for item in completed), errors=errors,
                   collection_status="FINISHED_WITH_EMPTY_WINDOWS" if len(completed)==len(entries) and any(item["status"]=="EMPTY" for item in completed)
                   else "FINISHED" if len(completed)==len(entries) else "PARTIAL",
                   complete_five_year_market_coverage_confirmed=False,
                   timestamp_convention="UNVERIFIED_OPEN_AND_CLOSE_LABEL_COUNTS_REPORTED",
                   trading_calendar_verified=False,
                   independently_verified=False, yearly=yearly,
                   fixed_contract_multiplier_event_count=None,
                   gamma_causation_established=False,
                   gamma_research_blocker="Rolling strike/expiry continuity is unverified; no fixed-contract 2x/3x/5x/10x events inferred",
                   scope=("NIFTY 50 index only" if all(e["series"] == "INDEX" for e in entries)
                          else "NIFTY 50 index plus selected rolling relative strikes, expiry flag and expiry code"),
                   note="Missing minutes refer only to observed dates; weekends, holidays, special sessions and absent dates are not authenticated.")
    atomic_json(root / "history_summary.json", summary)
    return summary



class BackfillError(RuntimeError):
    """A safe failure description assembled from controlled local fields."""

    def __init__(self, details, message):
        self.details = details
        super().__init__(message)


VALIDATION_RULES = {
    "Missing rolling-option data object", "Candle source must be an object",
    "Missing candle arrays", "Candle array lengths differ",
    "Optional candle array lengths differ", "Invalid epoch timestamp",
    "Boolean candle values", "Invalid numeric candle values",
    "Missing timestamp or OHLC", "Non-positive or missing OHLC/strike",
    "Invalid OHLC range", "Negative volume, OI or IV",
    "Duplicate or out-of-order timestamps",
    "Provider returned timestamps outside request window", "Non-minute timestamp",
}


def failure_details(entry, stage, exc):
    item = dict(series=entry["series"], start=entry["start"], end=entry["end"],
                endpoint=entry["endpoint"], stage=stage,
                reason="REQUEST_OR_VALIDATION_FAILED")
    if isinstance(exc, DhanAPIError):
        item.update(exc.diagnostic())
    elif isinstance(exc, OSError):
        item["reason"] = "LOCAL_IO_FAILED"
        item["errno"] = exc.errno if type(exc.errno) is int else None
    elif stage == "CANDLE_VALIDATION":
        item["reason"] = "CANDLE_VALIDATION_FAILED"
        rule = str(exc)
        item["validation_rule"] = rule if rule in VALIDATION_RULES else "UNCLASSIFIED_VALIDATION"
    elif stage == "CACHE":
        item["reason"] = "CACHE_INTEGRITY_FAILED"
    return item


def stop_with_report(root, entries, completed, errors, details, message):
    errors.append(details)
    # Retain the last failure even if a later index-only run replaces the summary.
    try:
        atomic_json(root / "last_failure.json",
                    dict(failed_at_ist=datetime.now(IST).isoformat(), error=details))
    except (OSError, ValueError):
        details["failure_file_write_failed"] = True
    try:
        write_report(root, entries, completed, errors)
    except (OSError, ValueError):
        details["summary_write_failed"] = True
    raise BackfillError(details, message) from None


def collect(entries, root, *, client=None, pause_seconds=1, max_requests=None, retry_empty=False):
    if pause_seconds < 0.25 or not math.isfinite(pause_seconds):
        raise ValueError("Use a finite delay of at least 0.25 seconds")
    if max_requests is not None and max_requests < 1:
        raise ValueError("max-requests must be positive")
    client = client if client is not None else DhanClient()
    if str(client.profile().get("dataPlan", "")).strip().lower() != "active":
        raise RuntimeError("Dhan Data API plan not active; no historical calls made")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    completed, errors, calls = [], [], 0
    for entry in entries:
        try:
            cache = load_cached(root, entry, retry_empty=retry_empty)
        except (ValueError, OSError) as exc:
            stop_with_report(root, entries, completed, errors,
                             failure_details(entry, "CACHE", exc),
                             "Cached data failed integrity checks. Files were retained")
        if cache is not None:
            completed.append(cache)
            print(f'SKIP cached {entry["series"]}: {entry["start"]} to {entry["end"]}', file=sys.stderr)
            continue
        if max_requests is not None and calls >= max_requests:
            # Continue scanning existing caches to report the entire requested range.
            continue
        time.sleep(pause_seconds)
        calls += 1
        stage = "REQUEST"
        try:
            raw = client._call("POST", entry["endpoint"], entry["payload"])
            stage = "CANDLE_VALIDATION"
            frame = parse_bars(raw, entry)
            stage = "CHUNK_STORAGE"
            completed.append(save_chunk(root, entry, raw, frame))
            print(f'SAVED {entry["series"]}: {entry["start"]} to {entry["end"]}, {len(frame)} rows', file=sys.stderr)
            stage = "REPORT_STORAGE"
            write_report(root, entries, completed, errors)
        except (RuntimeError, ValueError, OSError) as exc:
            stop_with_report(root, entries, completed, errors,
                             failure_details(entry, stage, exc),
                             "Backfill stopped. Earlier chunks remain resumable")
    return write_report(root, entries, completed, errors)


def main():
    parser = argparse.ArgumentParser(description="Five-year newest-first NIFTY research; no orders")
    parser.add_argument("--through", type=date.fromisoformat, default=datetime.now(IST).date())
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--strikes", nargs="+", default=["ATM"])
    parser.add_argument("--expiry-flag", choices=["WEEK", "MONTH"], default="WEEK")
    parser.add_argument("--expiry-code", type=int, choices=[1, 2, 3], default=1,
                        help="Expired rolling options: 1=near, 2=next, 3=far")
    parser.add_argument("--output-dir", default="data/raw/dhan/backfill/NIFTY")
    parser.add_argument("--pause-seconds", type=float, default=1)
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--retry-empty", action="store_true")
    parser.add_argument("--index-only", action="store_true", help="Collect index history independently of rolling options")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        today = datetime.now(IST).date()
        start = args.from_date or five_year_start(args.through)
        if args.through > today or start < five_year_start(today):
            raise ValueError("Choose a past/current range within Dhan's latest five-year window")
        entries = make_plan(start, args.through, [s.upper() for s in args.strikes], args.expiry_flag,
                            args.expiry_code, index_only=args.index_only)
        if not args.execute:
            print(json.dumps(dict(mode="DRY_RUN", requested_from=str(start), requested_through=str(args.through),
                                  planned_data_requests=len(entries), order="NEWEST_FIRST",
                                  newest=entries[:3], oldest=entries[-3:], network_requests=0, saved_files=0), indent=2))
            return
        load_local_credentials()
        report = collect(entries, args.output_dir, pause_seconds=args.pause_seconds,
                         max_requests=args.max_requests, retry_empty=args.retry_empty)
        print(json.dumps(report, indent=2, allow_nan=False))
    except BackfillError as exc:
        raise SystemExit("Dhan history stopped: " + json.dumps(exc.details) +
                         ". Earlier chunks remain cached; see last_failure.json and history_summary.json.") from None
    except DhanAPIError as exc:
        raise SystemExit("Dhan profile failed: " + json.dumps(exc.diagnostic())) from None
    except OSError as exc:
        details = dict(stage="LOCAL_SETUP_OR_REPORT_STORAGE", reason="LOCAL_IO_FAILED",
                       errno=exc.errno if type(exc.errno) is int else None)
        raise SystemExit("Dhan history failed: " + json.dumps(details)) from None
    except (RuntimeError, ValueError):
        raise SystemExit("Dhan history failed during setup/input validation. Check token/plan and dates; credentials were not printed.") from None


if __name__ == "__main__":
    main()
