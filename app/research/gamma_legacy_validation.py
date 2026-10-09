"""Read-only, in-memory validation of legacy second-level NIFTY CSVs.

Structural validity never proves authentic broker prices or precise option
Gamma. Do NOT feed a quarantined contract into the event study.
"""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
import re

import numpy as np
import pandas as pd

LEGACY = re.compile(
    r"^NIFTY(?P<yy>\d{2})(?P<month>\d)(?P<day>\d{2})"
    r"(?P<strike>\d{5})(?P<side>CE|PE)\.csv$"
)
COLUMNS = {"date", "time", "price", "volume", "oi"}


def audit_legacy_tick_csv(frame: pd.DataFrame, filename: str,
                          min_observed_minutes: int = 360) -> dict:
    """Only structural gate; never auto-admit historically unverified files."""
    base = {
        "filename": Path(filename).name,
        "eligible_for_gamma_research": False,
        "source_provenance": "UNVERIFIED",
        "independent_minute_prices": "NOT_VERIFIED",
    }
    m = LEGACY.fullmatch(base["filename"])
    if not m:
        return {**base, "status": "REJECT_FILENAME", "reasons": ["Unknown NIFTY contract filename"]}
    try:
        expiry = datetime(2000 + int(m["yy"]), int(m["month"]),
                          int(m["day"])).date()
    except ValueError:
        return {**base, "status": "REJECT_EXPIRY", "reasons": ["Invalid expiry calendar date"]}
    contract = {
        "inferred_expiry": expiry.isoformat(),
        "inferred_strike": int(m["strike"]),
        "inferred_side": m["side"],
        "identity_source": "FILENAME_ONLY",
    }
    if not COLUMNS.issubset(frame.columns):
        return {**base, **contract, "status": "REJECT_COLUMNS",
                "reasons": ["Must have date,time,price,volume,oi"]}
    if frame.empty:
        return {**base, **contract, "status": "REJECT_EMPTY",
                "reasons": ["File is empty"]}
    ts = pd.to_datetime(
        frame.date.astype(str) + " " + frame.time.astype(str),
        format="%Y-%m-%d %H:%M:%S", errors="coerce",
    )
    num = frame[["price", "volume", "oi"]].apply(pd.to_numeric, errors="coerce")
    arr = num.to_numpy(dtype=float)
    invalid = (ts.isna().to_numpy() | ~np.isfinite(arr).all(axis=1) |
               (arr[:, 0] <= 0) | (arr[:, 1:] < 0).any(axis=1))
    dates = sorted(ts.dropna().dt.date.unique())
    after = int((ts.dt.date > expiry).fillna(False).sum())
    outside = int(((ts.dt.time < time(9, 15)) |
                   (ts.dt.time > time(15, 29, 59))).fillna(False).sum())
    reversals = int((ts.diff() < pd.Timedelta(0)).sum())
    observed = int(ts.dropna().dt.floor("min").nunique())
    dup = int(frame.duplicated(["date", "time", "price", "volume", "oi"]).sum())
    diffs = int(pd.DataFrame({"sec": ts, "price": num.price})
                .groupby("sec").price.nunique().gt(1).sum())
    reasons = []
    if invalid.any():
        reasons.append(f"{int(invalid.sum())} invalid numeric/time records")
    if after:
        reasons.append(f"{after} rows after expiry")
    if outside:
        reasons.append(f"{outside} rows outside regular NSE session")
    if reversals:
        reasons.append(f"{reversals} backwards timestamps")
    if len(dates) != 1:
        reasons.append("Pilot requires one trading day per CSV")
    if observed < min_observed_minutes:
        reasons.append(f"Sparse: {observed} of 375 expected regular-session minutes")
    prices = num.price.dropna()
    if ((prices * 20 - (prices * 20).round()).abs() > 1e-7).any():
        reasons.append("Option prices not aligned to historical 0.05 tick grid")
    vols = num.volume.dropna()
    if ((vols % 50).abs() > 1e-7).any():
        reasons.append("Some volumes not multiples of historical lot 50")
    return {
        **base, **contract,
        "status": (
            "QUARANTINED_STRUCTURAL_OK_NEEDS_INDEPENDENT_REFERENCE"
            if not reasons else "REJECT_STRUCTURAL_OR_COVERAGE"
        ),
        "records": len(frame),
        "observed_dates": [d.isoformat() for d in dates],
        "observed_minutes": observed,
        "expected_minutes": 375,
        "identical_duplicate_rows": dup,
        "same_second_multiple_prices": diffs,
        "min_premium": float(prices.min()) if not prices.empty else None,
        "max_premium": float(prices.max()) if not prices.empty else None,
        "reasons": reasons or [
            "Structural checks passed; original broker identity and prices "
            "are NOT independently verified"
        ],
        "note": (
            "Second-level ticks are NOT complete 1-minute OHLC or historical "
            "Greeks. Same-second trade ordering, per-tick volume and source "
            "authenticity remain unverified. No event training or 5x claim."
        ),
    }
