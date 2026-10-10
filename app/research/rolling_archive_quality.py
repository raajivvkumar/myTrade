"""Fail-closed, read-only quality gates for archived Dhan *rolling* option candles.

This module NEVER reconstructs fixed-expiry contracts, Gamma Greeks, fills, or
P&L from ATM-relative data. All original ZIP members remain immutable.
Session overrides are explicitly curated exceptions, not a complete NSE
holiday calendar. Add verified exchange calendars before production research.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd


SOURCE_REQUIRED = frozenset({
    "timestamp", "open", "high", "low", "close", "actual_strike", "volume"
})
ROLLING_ONLY_STATUS = "ROLLING_PROXY_ONLY_FIXED_CONTRACT_RETURNS_BLOCKED"

# Intervals are [open, close): an observation exactly at the end is flagged
# separately as a boundary candidate, not an ordinary traded minute.
SPECIAL_SESSIONS: dict[date, tuple[tuple[str, str], ...]] = {
    date(2021, 11, 4): (("18:15", "19:15"),),
    date(2022, 10, 24): (("18:15", "19:15"),),
    date(2023, 11, 12): (("18:15", "19:15"),),
    date(2024, 1, 20): (("09:15", "15:30"),),
    date(2024, 3, 2): (("09:15", "10:00"), ("11:30", "12:30")),
    date(2024, 5, 18): (("09:15", "10:00"), ("11:30", "12:30")),
    date(2024, 11, 1): (("18:00", "19:00"),),
    date(2025, 2, 1): (("09:15", "15:30"),),
    date(2025, 10, 21): (("13:45", "14:45"),),
    date(2026, 2, 1): (("09:15", "15:30"),),
}
EXTENDED_CLOSE_EFFECTIVE = date(2026, 8, 3)
FLAGS = (
    "q_outside_session", "q_closing_boundary", "q_strike_switched",
    "q_zero_volume", "q_zero_volume_nonflat",
    "q_zero_volume_repeated_close", "q_source_negative_volume",
)


def _minutes(hhmm: str) -> int:
    hour, minute = map(int, hhmm.split(":"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Session times must be HH:MM")
    return 60 * hour + minute


def sessions_for_day(day: date, *, overrides=None, closed_dates=()):
    """Return explicitly supported session windows; weekday holidays need a calendar.

    overrides[date] may be a tuple of ("HH:MM", "HH:MM") windows or an
    empty tuple for a holiday; closed_dates separately marks closures.
    Overrides take precedence over closed_dates.
    """
    if overrides is not None and day in overrides:
        return tuple(overrides[day])
    if day in SPECIAL_SESSIONS:
        return SPECIAL_SESSIONS[day]
    if day in closed_dates or day.weekday() >= 5:
        return ()
    end = "15:40" if day >= EXTENDED_CLOSE_EFFECTIVE else "15:30"
    return (("09:15", end),)


def classify_rolling_frame(
    frame: pd.DataFrame, *, interval_minutes: int,
    sessions_by_date=None, closed_dates=(),
) -> pd.DataFrame:
    """Annotate one 1m/5m rolling CSV, retaining every original row and order.

    The proxy_eligible column allows descriptive research only; it is NOT
    permission for order fills or fixed-contract multiplier calculations.
    """
    if interval_minutes not in (1, 5):
        raise ValueError("Only 1m and 5m rolling data are supported")
    missing = SOURCE_REQUIRED.difference(frame.columns)
    if missing:
        raise ValueError("Missing archive columns: " + ", ".join(sorted(missing)))

    out = frame.copy(deep=True)
    if out.empty:
        for col in (*FLAGS, "proxy_eligible"):
            out[col] = pd.Series(dtype=bool)
        return out

    stamps = pd.to_datetime(out["timestamp"], errors="coerce")
    if stamps.isna().any():
        raise ValueError("Invalid timestamps")
    if stamps.dt.tz is not None:
        stamps = stamps.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    if not stamps.is_monotonic_increasing or stamps.duplicated().any():
        raise ValueError("Unsorted or duplicate timestamps")
    if stamps.dt.second.ne(0).any() or stamps.dt.microsecond.ne(0).any():
        raise ValueError("Non-minute timestamp")
    if interval_minutes == 5 and stamps.dt.minute.mod(5).ne(0).any():
        raise ValueError("5m timestamps must align to five-minute intervals")

    numeric = {}
    for col in ("open", "high", "low", "close", "actual_strike", "volume"):
        numeric[col] = pd.to_numeric(out[col], errors="coerce")
    o, h, l, c, strike, volume = (numeric[x] for x in (
        "open", "high", "low", "close", "actual_strike", "volume"))
    for col in ("open", "high", "low", "close", "actual_strike"):
        if (~np.isfinite(numeric[col]) | numeric[col].le(0)).any():
            raise ValueError("Invalid OHLC or strike")
    if (l.gt(pd.concat([o, c], axis=1).min(axis=1)) |
            h.lt(pd.concat([o, c], axis=1).max(axis=1)) |
            l.gt(h)).any():
        raise ValueError("Invalid OHLC high/low bounds")
    if np.isinf(volume).any() or volume.lt(0).any():
        raise ValueError("Unmasked negative or infinite volume")

    days = stamps.dt.date
    minutes = stamps.dt.hour * 60 + stamps.dt.minute
    inside = pd.Series(False, index=frame.index, dtype=bool)
    boundary = pd.Series(False, index=frame.index, dtype=bool)
    for day in days.unique():
        day_mask = days.eq(day)
        windows = sessions_for_day(
            day, overrides=sessions_by_date, closed_dates=closed_dates)
        for start, end in windows:
            start_min, end_min = _minutes(start), _minutes(end)
            if end_min <= start_min:
                raise ValueError("Session end must be after start")
            inside |= day_mask & minutes.ge(start_min) & minutes.lt(end_min)
            boundary |= day_mask & minutes.eq(end_min)

    previous_strike = strike.shift()
    same_day_previous = days.eq(days.shift())
    out["q_strike_switched"] = same_day_previous & strike.ne(previous_strike)
    out["q_closing_boundary"] = boundary
    out["q_outside_session"] = ~(inside | boundary)
    out["q_zero_volume"] = volume.eq(0)
    flat = o.eq(h) & o.eq(l) & o.eq(c)
    out["q_zero_volume_nonflat"] = out["q_zero_volume"] & ~flat

    # Shift inside each day/strike, not from the last bar of another strike or
    # yesterday. A same-strike spot change is possible while premium is stale.
    previous_close = c.groupby([days, strike], sort=False).shift()
    out["q_zero_volume_repeated_close"] = (
        out["q_zero_volume"] & previous_close.notna() & c.eq(previous_close)
    )
    negative_flag = out.get("volume_invalid_negative")
    if negative_flag is None:
        out["q_source_negative_volume"] = False
    else:
        if negative_flag.isna().any() or not negative_flag.isin([True, False]).all():
            raise ValueError("volume_invalid_negative must contain booleans")
        out["q_source_negative_volume"] = negative_flag.astype(bool)
    # Missing volume from source is not executable and is not a zero-volume bar.
    out["proxy_eligible"] = (
        inside & volume.gt(0) & ~out["q_strike_switched"] &
        ~out["q_source_negative_volume"]
    )
    return out


def require_fixed_contract_for_returns(frame: pd.DataFrame, *,
                                       independent_source_verified=False):
    """Reject rolling data even if a caller adds an apparent contract ID.

    Independent evidence is a separate prerequisite. Successful admission
    still does NOT prove historical Gamma, quoted liquidity or executable P&L.
    """
    if "actual_strike" in frame.columns or "series" in frame.columns:
        raise ValueError("Rolling history cannot validate fixed-contract returns")
    required = {"instrument_key", "expiry", "strike_price", "option_type"}
    if not required.issubset(frame.columns):
        raise ValueError("Fixed-contract ID, expiry, strike and side required")
    if frame.empty or not independent_source_verified:
        raise ValueError("Independent historical contract-source verification required")
    for key in required:
        if frame[key].isna().any() or frame[key].nunique() != 1:
            raise ValueError("History must reference one unchanging fixed contract")
    return True


def inspect_archive(path: str | Path) -> dict:
    """Read an existing quarterly ZIP member-by-member; never extract or write."""
    path = Path(path)
    counters: Counter = Counter()
    total_rows = 0
    intervals: Counter = Counter()
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Archive has duplicate names or lacks manifest")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("fixed_option_contract_expiry_verified") is not False:
            raise ValueError("Expected explicitly unverified rolling archive")
        meta_names = [n for n in names if n.startswith("meta/") and n.endswith(".json")]
        csv_names = [n for n in names if n.endswith(".csv")]
        if len(meta_names) != manifest["requests_with_successful_responses"]:
            raise ValueError("Request metadata count differs from manifest")
        if len(csv_names) != len(meta_names) - manifest["empty_responses"]:
            raise ValueError("CSV/empty response count differs from manifest")
        for name in csv_names:
            if name.startswith("1m/"):
                interval = 1
            elif name.startswith("5m/"):
                interval = 5
            else:
                raise ValueError("Unexpected CSV interval")
            meta_name = "meta/" + name[:-4] + ".json"
            if meta_name not in names:
                raise ValueError("CSV missing its request metadata")
            request = json.loads(archive.read(meta_name))
            if request["status"] != "ROWS" or request["csv"] != name:
                raise ValueError("CSV metadata not consistent with request")
            with archive.open(name) as src:
                raw = pd.read_csv(src, low_memory=False)
            if len(raw) != request["rows"]:
                raise ValueError("Actual CSV rows differ from request metadata")
            labeled = classify_rolling_frame(raw, interval_minutes=interval)
            intervals[f"{interval}m"] += len(labeled)
            for flag in FLAGS:
                counters[flag] += int(labeled[flag].sum())
            counters["proxy_eligible_rows"] += int(labeled["proxy_eligible"].sum())
            total_rows += len(labeled)
            del raw, labeled
        if total_rows != manifest["rows_exported"]:
            raise ValueError("CSV row count differs from quarterly manifest")
    return {
        "quarter": manifest["quarter"],
        "status": ROLLING_ONLY_STATUS,
        "input_zip": str(path),
        "source": manifest["source"],
        "read_only": True,
        "broker_calls": 0,
        "orders": 0,
        "verified_gamma_events": None,
        "fixed_contract_returns_enabled": False,
        "csv_files": len(csv_names),
        "rows": total_rows,
        "interval_rows": dict(intervals),
        "flags": dict(counters),
        "warning": "Proxy-eligible is descriptive only; never executable fills.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", nargs="+", type=Path, required=True,
                        help="Existing local quarterly ZIP paths (read-only)")
    args = parser.parse_args(argv)
    reports = [inspect_archive(path) for path in args.zip]
    print(json.dumps({
        "status": ROLLING_ONLY_STATUS,
        "quarterly_reports": reports,
        "total_rows": sum(item["rows"] for item in reports),
        "verified_gamma_events": None,
        "orders": 0,
    }, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
