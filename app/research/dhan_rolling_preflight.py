"""Audit Dhan rolling-option cache before fixed-contract Gamma research.

Rolling ATM bars lack a verified expiry and instrument key. Even a constant
strike cannot establish continuity across an expiry roll. This module only
inspects local quality; it NEVER labels option-premium multipliers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED = {"timestamp", "series", "actual_strike", "open", "high", "low", "close"}
OPTIONAL = ("volume", "oi", "iv")


def audit_rolling_frame(frame: pd.DataFrame, expected_series: str) -> dict:
    """Validate one provider chunk, without converting it to fixed-contract data."""
    missing = sorted(REQUIRED - set(frame.columns))
    if missing:
        raise ValueError("Missing Dhan rolling fields: " + ", ".join(missing))
    if frame.empty:
        return {
            "rows": 0, "sessions": 0, "strike_switches": 0,
            "optional_missing": {key: 0 for key in OPTIONAL},
            "fixed_contract_eligible": False,
        }
    if frame.series.isna().any() or not frame.series.eq(expected_series).all():
        raise ValueError("Unexpected or mixed Dhan rolling series")
    stamps = pd.to_datetime(frame.timestamp, errors="coerce")
    if stamps.isna().any() or not isinstance(stamps.dtype, np.dtype) or stamps.dt.tz is not None:
        raise ValueError("Rolling timestamps must be local IST naive timestamps")
    if stamps.duplicated().any() or not stamps.is_monotonic_increasing:
        raise ValueError("Duplicate or unordered timestamps in rolling chunk")
    if (stamps.dt.second.ne(0) | stamps.dt.microsecond.ne(0)).any():
        raise ValueError("Non-minute timestamp in rolling chunk")
    numeric = frame[list(REQUIRED - {"timestamp", "series"})].apply(
        pd.to_numeric, errors="coerce"
    )
    if not np.isfinite(numeric.to_numpy(dtype=float)).all() or numeric.le(0).any().any():
        raise ValueError("Invalid price/strike in rolling chunk")
    if (numeric.low > numeric[["open", "close"]].min(axis=1)).any() or (
        numeric.high < numeric[["open", "close"]].max(axis=1)
    ).any():
        raise ValueError("Invalid OHLC in rolling chunk")
    switches = 0
    for _, loc in frame.assign(_session=stamps.dt.date).groupby("_session", sort=True):
        strikes = pd.to_numeric(loc.actual_strike)
        switches += int(strikes.ne(strikes.shift()).sum() - 1)
    return {
        "rows": int(len(frame)),
        "sessions": int(stamps.dt.date.nunique()),
        "strike_switches": int(switches),
        "optional_missing": {
            key: int(pd.to_numeric(frame[key], errors="coerce").isna().sum())
            if key in frame else int(len(frame))
            for key in OPTIONAL
        },
        # NOT promoted even if someone injects 'expiry' into the Parquet:
        # provider provenance of actual fixed security identity is absent.
        "fixed_contract_eligible": False,
    }


def inspect_dhan_cache(root: Path) -> dict:
    """Read local rolling Parquet chunks; never write, network, train, or trade."""
    root = Path(root).expanduser()
    if not root.is_dir():
        raise ValueError("Dhan backfill root does not exist")
    chunks = sorted(
        p for p in (root / "chunks").glob("*/*.parquet")
        if p.parent.name != "INDEX" and p.is_file()
    )
    report = {
        "status": "NO_ROLLING_CHUNKS",
        "source": "LOCAL_DHAN_EXPIRED_ROLLING_OPTION_PARQUET",
        "parquet_chunks_found": len(chunks),
        "chunks_checked": 0,
        "chunks_quarantined": 0,
        "rows_checked": 0,
        "strike_switches_within_sessions": 0,
        "missing_optional_cells": {key: 0 for key in OPTIONAL},
        "files": [],
        "fixed_contract_identity_verified": False,
        "historical_gamma_available_from_endpoint": False,
        "eligible_fixed_contract_windows": 0,
        "validated_2x_events": None,
        "validated_3x_events": None,
        "validated_5x_events": None,
        "validated_10x_events": None,
        "prediction_accuracy": None,
        "reason": (
            "Dhan rolling bars expose actual strike, but neither a verified "
            "per-bar NSE instrument key nor the exact historical contract expiry. "
            "Same-strike runs may cross expiry rolls."
        ),
    }
    seen = set()
    for path in chunks:
        item = {"series": path.parent.name, "file": path.name}
        try:
            frame = pd.read_parquet(path)
            quality = audit_rolling_frame(frame, path.parent.name)
            # Cross-chunk collisions are counted, not silently de-duplicated.
            stamps = pd.to_datetime(frame.timestamp)
            collisions = sum(
                (path.parent.name, stamp) in seen for stamp in stamps
            )
            seen.update((path.parent.name, stamp) for stamp in stamps)
            item.update(quality)
            item["cross_chunk_duplicate_timestamps"] = int(collisions)
            report["chunks_checked"] += 1
            report["rows_checked"] += quality["rows"]
            report["strike_switches_within_sessions"] += quality["strike_switches"]
            for field in OPTIONAL:
                report["missing_optional_cells"][field] += quality["optional_missing"][field]
        except (ValueError, TypeError, KeyError, ImportError, OSError) as exc:
            item["quarantine"] = type(exc).__name__
            report["chunks_quarantined"] += 1
        report["files"].append(item)
    if chunks:
        report["status"] = (
            "QUARANTINED_INVALID_ROLLING_CHUNKS" if report["chunks_quarantined"]
            else "BLOCKED_FIXED_CONTRACT_EXPIRY_UNVERIFIED"
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("data/raw/dhan/backfill/NIFTY"),
        help="Local backfill root; no token, network, or writes",
    )
    args = parser.parse_args()
    print(json.dumps(inspect_dhan_cache(args.root), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
