"""Read-only LOCAL bulk scan of candidate fixed NIFTY option minute CSVs.

Classifies legacy tick data and daily reports as unverified, never promotes
them to 1-minute OHLC. Supports nested ZIP inputs but does not extract files,
persist results, fetch brokers, or trade. RAR archives must be extracted
separately by the user into a trusted directory before scanning.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import json
from pathlib import Path
import zipfile

import pandas as pd

from app.research.gamma_event_study import investigate_events, _count_conditions

REQUIRED_MINUTE = frozenset({
    "timestamp", "open", "high", "low", "close", "volume", "oi",
    "instrument_key", "strike_price", "option_type", "expiry",
})
TICK = frozenset({"date", "time", "price", "volume", "oi"})
MAX_CSV_BYTES = 100_000_000
MAX_ZIP_DEPTH = 3


def _schema(data: bytes) -> str:
    try:
        first = data.split(b"\n", 1)[0].decode("utf-8-sig")
        fields = {x.strip().lower() for x in next(csv.reader([first]))}
    except (UnicodeError, csv.Error, StopIteration):
        return "UNREADABLE_CSV_HEADER"
    if REQUIRED_MINUTE.issubset(fields):
        return "EXACT_OPTION_MINUTE_CANDIDATE"
    if TICK.issubset(fields):
        return "TICK_SCHEMA_QUARANTINED_NOT_OHLC"
    if {"open", "high", "low", "close"}.issubset(fields):
        return "OTHER_OHLC_NO_EXACT_MINUTE_CONTRACT"
    return "OTHER_CSV_NO_CONTRACT_CANDLES"


def _empty_report() -> dict:
    return {
        "status": "EXPLORATORY_UNVERIFIED_SOURCE_NO_PREDICTIVE_CLAIM",
        "source_note": (
            "Files are structurally inspected only; no independent NSE/broker "
            "minute-level authenticity, bid/ask fills, gamma causality or P&L."
        ),
        "read_only": True,
        "external_network_requests": 0,
        "orders": 0,
        "file_status_counts": {},
        "scanned_files": 0,
        "exact_contract_minute_files_accepted_structurally": 0,
        "eligible_start_minutes": 0,
        "observed_2x_events": 0,
        "observed_3x_events": 0,
        "observed_5x_events": 0,
        "observed_10x_events": 0,
        "matched_below_2x_controls": 0,
        "unique_event_dates": 0,
        "unique_expiries": 0,
        "unique_fixed_contract_keys": 0,
        "candidate_condition_comparison": {},
        "source_files": [],
        "unscanned_container_types": {},
    }


def scan_directory(root: Path, *, horizon: int = 30,
                   min_premium: float = 2.0) -> dict:
    """Scan each local eligible file exactly once, with no on-disk writes."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("A local, existing source directory is required")
    if not 1 <= horizon <= 120 or min_premium <= 0:
        raise ValueError("Horizon must be 1..120 and premium positive")
    output = _empty_report()
    counts: Counter = Counter()
    blocked: Counter = Counter()
    hashes = set()
    seen_contract_days = set()
    events_list = []
    controls_list = []
    unique_dates = set()
    unique_expiries = set()
    keys = set()

    def record(label: str, kind: str, **extra) -> None:
        counts[kind] += 1
        output["source_files"].append({"file": label, "status": kind, **extra})

    def handle_csv(label: str, payload: bytes) -> None:
        if len(payload) > MAX_CSV_BYTES:
            record(label, "CSV_TOO_LARGE_REQUIRES_SPLIT")
            return
        digest = sha256(payload).hexdigest()
        if digest in hashes:
            record(label, "DUPLICATE_SOURCE_BYTES_SKIPPED")
            return
        hashes.add(digest)
        status = _schema(payload)
        if status != "EXACT_OPTION_MINUTE_CANDIDATE":
            record(label, status)
            return
        try:
            frame = pd.read_csv(BytesIO(payload), low_memory=False)
            frame.columns = [str(c).strip().lower() for c in frame.columns]
            if frame.empty or frame["timestamp"].isna().any():
                raise ValueError("Empty or missing timestamps")
            identity = tuple(
                str(frame[c].iloc[0]) for c in (
                    "instrument_key", "strike_price", "option_type", "expiry"
                )
            )
            if frame["instrument_key"].astype(str).str.startswith("NSE_FO|").eq(False).any():
                raise ValueError("Non-option NSE_FO identity")
            days = set(pd.to_datetime(frame["timestamp"], errors="raise").dt.date)
            if not days:
                raise ValueError("No dated observations")
            overlap = {(identity, day) for day in days} & seen_contract_days
            if overlap:
                raise ValueError(
                    "Same contract/date overlaps a previous source; merge/deduplicate first"
                )
            events, controls, summary = investigate_events(
                [frame], horizon=horizon, min_premium=min_premium,
            )
            seen_contract_days.update((identity, day) for day in days)
        except (ValueError, TypeError, KeyError, AttributeError, pd.errors.ParserError) as exc:
            # No individual broker data or secrets in errors.
            record(label, "MINUTE_SCHEMA_REJECTED", issue=type(exc).__name__)
            return

        output["exact_contract_minute_files_accepted_structurally"] += 1
        output["eligible_start_minutes"] += int(summary["eligible_windows"])
        for threshold in (2, 3, 5, 10):
            output[f"observed_{threshold}x_events"] += int(
                summary[f"observed_{threshold}x_events"]
            )
        output["matched_below_2x_controls"] += int(summary["matched_controls"])
        if not events.empty:
            events_list.append(events)
            unique_dates.update(pd.to_datetime(events.signal_ist).dt.date)
            unique_expiries.update(str(x) for x in events.expiry)
            keys.update(str(x) for x in events.instrument_key)
        if not controls.empty:
            controls_list.append(controls)
        record(label, "MINUTE_SCHEMA_STRUCTURAL_ONLY_NOT_SOURCE_VERIFIED",
               eligible_start_minutes=int(summary["eligible_windows"]),
               selected_2x_episodes=int(summary["observed_2x_events"]))

    def scan_zip(label: str, payload: bytes, depth: int) -> None:
        if depth > MAX_ZIP_DEPTH:
            record(label, "NESTED_ZIP_DEPTH_LIMIT")
            return
        try:
            z = zipfile.ZipFile(BytesIO(payload))
        except (ValueError, zipfile.BadZipFile):
            record(label, "INVALID_ZIP")
            return
        with z:
            for member in z.infolist():
                if member.is_dir():
                    continue
                name = member.filename.lower()
                child = label + "::" + member.filename
                if name.endswith((".rar", ".7z")):
                    blocked["RAR_OR_7Z_REQUIRES_LOCAL_EXTRACTION"] += 1
                    continue
                if not name.endswith((".csv", ".zip")):
                    continue
                if member.file_size > MAX_CSV_BYTES:
                    record(child, "CONTAINER_MEMBER_TOO_LARGE")
                    continue
                try:
                    data = z.read(member)
                except (RuntimeError, ValueError, zipfile.BadZipFile, OSError):
                    record(child, "CONTAINER_READ_ERROR")
                    continue
                if name.endswith(".csv"):
                    handle_csv(child, data)
                else:
                    scan_zip(child, data, depth + 1)

    for file in sorted(root.rglob("*")):
        if not file.is_file():
            continue
        suffix = file.suffix.lower()
        if suffix not in (".csv", ".zip", ".rar", ".7z"):
            continue
        label = str(file.relative_to(root)).replace("\\", "/")
        if suffix in (".rar", ".7z"):
            blocked["RAR_OR_7Z_REQUIRES_LOCAL_EXTRACTION"] += 1
            continue
        if file.stat().st_size > MAX_CSV_BYTES:
            record(label, "FILE_TOO_LARGE_REQUIRES_SPLIT")
            continue
        if suffix == ".csv":
            handle_csv(label, file.read_bytes())
        else:
            scan_zip(label, file.read_bytes(), 1)

    output["file_status_counts"] = dict(sorted(counts.items()))
    output["scanned_files"] = len(output["source_files"])
    output["unscanned_container_types"] = dict(sorted(blocked.items()))
    output["unique_event_dates"] = len(unique_dates)
    output["unique_expiries"] = len(unique_expiries)
    output["unique_fixed_contract_keys"] = len(keys)
    observed = pd.concat(events_list, ignore_index=True) if events_list else pd.DataFrame()
    negatives = pd.concat(controls_list, ignore_index=True) if controls_list else pd.DataFrame()
    positive_conditions = _count_conditions(observed)
    negative_conditions = _count_conditions(negatives)
    for name, event in positive_conditions.items():
        other = negative_conditions[name]
        output["candidate_condition_comparison"][name] = {
            "events": event,
            "matched_below_2x_controls": other,
            "rate_difference_descriptive": (
                round(event["rate"] - other["rate"], 4)
                if event["rate"] is not None and other["rate"] is not None
                else None
            ),
        }
    output["horizon_minutes"] = horizon
    output["minimum_entry_premium"] = min_premium
    return output


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, type=Path,
                   help="Directory of actual fixed-contract minute CSV files; no data copied")
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--min-premium", type=float, default=2.0)
    args = p.parse_args()
    print(json.dumps(
        scan_directory(args.root, horizon=args.horizon,
                       min_premium=args.min_premium),
        ensure_ascii=False, indent=2, allow_nan=False
    ))


if __name__ == "__main__":
    main()
