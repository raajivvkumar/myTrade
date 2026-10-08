"""NIFTY 1-minute archive audit; offline, no broker credentials or network.

Checks internal quality only. Independent exchange verification is always
NOT_PERFORMED unless an independently supplied minute-level reference CSV
is explicitly provided. No fabricated candles or backtest signals.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path

import pandas as pd

from app.data.market_archive import verify_backup

FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "oi")
PRICES = ("open", "high", "low", "close")
EXPECTED_START = time(9, 15)
EXPECTED_END = time(15, 29)
SAMPLE_LIMIT = 25


def _timestamps(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    if not pd.api.types.is_datetime64_any_dtype(parsed):
        raise ValueError("Mixed/invalid timestamp representations")
    return parsed


def _read_file(path: Path) -> pd.DataFrame:
    manifest = path.with_suffix(".json")
    if not manifest.is_file():
        raise ValueError(f"Missing source manifest: {manifest}")
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    if (meta.get("data_kind") != "INDEX_CANDLES_NOT_OPTION_CONTRACT"
            or meta.get("index") != "NIFTY"
            or meta.get("interval_minutes") != 1):
        raise ValueError(f"Wrong instrument, source type or interval: {path}")
    frame = pd.read_parquet(path)
    if not set(FIELDS).issubset(frame.columns):
        raise ValueError(f"Required columns missing: {path}")
    frame = frame.loc[:, FIELDS].copy()
    frame["timestamp"] = _timestamps(frame["timestamp"])
    for name in FIELDS[1:]:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    if frame.isna().any().any():
        raise ValueError(f"Null or non-numeric fields in {path}")
    if (frame[list(PRICES)] <= 0).any().any():
        raise ValueError(f"Nonpositive OHLC in {path}")
    if (frame[["volume", "oi"]] < 0).any().any():
        raise ValueError(f"Negative volume / OI in {path}")
    if ((frame["low"] > frame[["open", "close"]].min(axis=1))
            | (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["high"] < frame["low"])).any():
        raise ValueError(f"Invalid OHLC ordering in {path}")
    if len(frame) != meta.get("rows"):
        raise ValueError(f"Manifest row count differs from Parquet: {path}")
    if len(frame) and (str(frame.timestamp.min()) != meta.get("first_timestamp_ist")
                       or str(frame.timestamp.max()) != meta.get("last_timestamp_ist")):
        raise ValueError(f"Manifest start/end timestamps differ from Parquet: {path}")
    return frame


def _read_reference(path: Path) -> pd.DataFrame:
    reference = pd.read_csv(path)
    required = {"timestamp", *PRICES}
    if not required.issubset(reference.columns):
        raise ValueError("Reference CSV must contain timestamp,open,high,low,close")
    reference = reference[["timestamp", *PRICES]].copy()
    reference.timestamp = _timestamps(reference.timestamp)
    for name in PRICES:
        reference[name] = pd.to_numeric(reference[name], errors="coerce")
    if reference.isna().any().any() or reference.timestamp.duplicated().any():
        raise ValueError("Reference has missing/duplicate timestamps or invalid prices")
    return reference


def audit_index(source: Path, *, reference_csv: Path | None = None,
                point_tolerance: float = 0.5) -> tuple[dict, list[dict]]:
    """Audit real archived rows, never silently repair/crop input.

    Weekends/holidays without any rows are not invented as missing sessions.
    Full observed sessions use the NORMAL 09:15..15:29 IST minute grid.
    Extraordinary trading sessions require a separate NSE calendar review.
    """
    source = Path(source)
    if not source.is_dir():
        raise FileNotFoundError(f"Archive directory does not exist: {source}")
    if point_tolerance < 0:
        raise ValueError("Point tolerance must be nonnegative")
    parquet_files = sorted(source.glob("NIFTY/1minute/*.parquet"))
    if not parquet_files:
        raise ValueError(f"No 1-minute NIFTY index Parquet files found in {source}")
    checksum_verified = None
    if (source / "archive_catalogue.json").is_file():
        checksum_verified = verify_backup(source)
    frames = [_read_file(path) for path in parquet_files]
    frame = pd.concat(frames, ignore_index=True)
    if frame.empty:
        raise ValueError("No candle observations found in archive")
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    duplicate_count = int(frame.timestamp.duplicated().sum())
    conflicts = []
    for timestamp, group in frame.loc[
            frame.timestamp.duplicated(keep=False)].groupby("timestamp"):
        if len(group[list(FIELDS[1:])].drop_duplicates()) > 1:
            conflicts.append(str(timestamp))
    if conflicts:
        raise ValueError(
            "Conflicting OHLC/volume/OI for same timestamp; first examples: "
            + ", ".join(conflicts[:3])
        )
    unique = frame.drop_duplicates("timestamp").reset_index(drop=True)
    unique["session_date"] = unique.timestamp.dt.date
    daily = []
    gap_sample = []
    off_session_sample = []
    for d, group in unique.groupby("session_date", sort=True):
        lower = pd.Timestamp(datetime.combine(d, EXPECTED_START))
        upper = pd.Timestamp(datetime.combine(d, EXPECTED_END))
        inside = group.loc[group.timestamp.between(lower, upper)]
        outside = group.loc[~group.timestamp.between(lower, upper)]
        expected = pd.date_range(lower, upper, freq="min")
        missing = expected.difference(inside.timestamp)
        gap_sample.extend(str(ts) for ts in missing[:max(0, SAMPLE_LIMIT-len(gap_sample))])
        off_session_sample.extend(
            str(ts) for ts in outside.timestamp.iloc[:max(0, SAMPLE_LIMIT-len(off_session_sample))]
        )
        regular = inside.sort_values("timestamp")
        daily.append({
            "date": d.isoformat(),
            "observed": int(len(group)),
            "regular_session_rows": int(len(regular)),
            "expected_regular_session_minutes": int(len(expected)),
            "missing_regular_session_minutes": int(len(missing)),
            "outside_regular_session_minutes": int(len(outside)),
            "first_timestamp_ist": str(group.timestamp.min()),
            "last_timestamp_ist": str(group.timestamp.max()),
            "open": float(regular.open.iloc[0]) if len(regular) else None,
            "high": float(regular.high.max()) if len(regular) else None,
            "low": float(regular.low.min()) if len(regular) else None,
            "last_minute_close": float(regular.close.iloc[-1]) if len(regular) else None,
        })
    report = {
        "dataset": "UPSTOX_HISTORICAL_V3_NIFTY_INDEX_1MIN_NOT_OPTIONS",
        "quality_status": "UNVALIDATED_NEEDS_INDEPENDENT_NSE_CHECK",
        "files": len(parquet_files),
        "raw_rows": int(len(frame)),
        "unique_rows": int(len(unique)),
        "duplicate_rows_identical": duplicate_count,
        "observed_session_dates": len(daily),
        "observed_regular_session_missing_minutes": sum(x["missing_regular_session_minutes"] for x in daily),
        "outside_regular_session_minutes": sum(x["outside_regular_session_minutes"] for x in daily),
        "examples_missing_ist": gap_sample[:SAMPLE_LIMIT],
        "examples_outside_session_ist": off_session_sample[:SAMPLE_LIMIT],
        "backup_checksum_files_verified": checksum_verified,
        "reference_comparison": "NOT_PERFORMED",
        "coverage_warning": (
            "No NSE holiday/calendar supplied. Completely absent market sessions and "
            "special sessions cannot be classified. Expected 09:15..15:29 applies "
            "only to normal full NSE sessions."
        ),
        "trading_warning": "Index candles are not historical CE/PE option premium/Greek data.",
    }
    if reference_csv is not None:
        reference = _read_reference(Path(reference_csv))
        merged = unique.merge(reference, on="timestamp", how="inner",
                              suffixes=("_upstox", "_reference"))
        mismatch = pd.Series(False, index=merged.index)
        for name in PRICES:
            mismatch |= ((merged[name + "_upstox"]
                          - merged[name + "_reference"]).abs() > point_tolerance)
        report["reference_comparison"] = "USER_SUPPLIED_REFERENCE_COMPARED_NOT_NSE_CERTIFIED"
        report["reference_overlapping_minutes"] = int(len(merged))
        report["reference_mismatched_minutes"] = int(mismatch.sum())
        report["reference_unmatched_archive_minutes"] = int(len(unique) - len(merged))
        report["reference_note"] = (
            "A user-supplied reference CSV was compared; authenticity and independence "
            "were not verified by this program."
        )
        if not len(merged):
            report["reference_comparison"] = "NO_OVERLAPPING_REFERENCE_MINUTES"
    return report, daily


def main():
    parser = argparse.ArgumentParser(
        description="Offline NIFTY 1m quality and gap report. No Upstox token needed."
    )
    parser.add_argument("--archive", type=Path,
                        default=Path("../MyTradeOfflineArchive/upstox_index_v3"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("../MyTradeOfflineArchive/reports"))
    parser.add_argument("--reference-csv", type=Path,
                        help="Optional independent minute OHLC CSV; authenticity not assumed")
    parser.add_argument("--tolerance", type=float, default=0.5,
                        help="Index-point difference for user-supplied reference")
    a = parser.parse_args()
    report, daily = audit_index(a.archive, reference_csv=a.reference_csv,
                                point_tolerance=a.tolerance)
    a.output_dir.mkdir(parents=True, exist_ok=True)
    out_json = a.output_dir / "nifty_1minute_quality.json"
    out_csv = a.output_dir / "nifty_1minute_sessions.csv"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    pd.DataFrame(daily).to_csv(out_csv, index=False)
    print(f"Audited {report['unique_rows']} real NIFTY minute candles "
          f"across {report['observed_session_dates']} observed dates")
    print(f"Normal-session missing minutes: {report['observed_regular_session_missing_minutes']}")
    print(f"Duplicate identical timestamps: {report['duplicate_rows_identical']}")
    print(f"Outside normal hours: {report['outside_regular_session_minutes']}")
    print(f"Independent reference: {report['reference_comparison']}")
    print(f"Quality status: {report['quality_status']}")
    print(f"Reports: {out_json} and {out_csv}")


if __name__ == "__main__":
    main()
