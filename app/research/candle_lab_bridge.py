"""Presentation-neutral Gamma research payload for the ORIGINAL myTrade Candle Lab.

The site UI is a separate project, not the Streamlit app. This module
formats already-fetched, IN-MEMORY research results into a strict JSON-safe
contract. It does NOT serve HTTP, read broker credentials, fetch market data,
place orders or write market history.

Only trusted server-side code can fetch Upstox data. NEVER send an Upstox
token to the hosted/browser Candle Lab.
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any

import pandas as pd

from app.research.gamma_lab import compare_chain_snapshots, normalize_chain

SCHEMA_VERSION = "1.0"
SOURCE = "UPSTOX_NIFTY_OPTION_CHAIN"


def _safe(value: Any) -> Any:
    """Remove NaN/Infinity from JSON. Missing data is NULL, not zero."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Snapshot retrieval time must include timezone")
        return value.isoformat()
    if isinstance(value, (float, int)):
        if not math.isfinite(float(value)):
            return None
        return value.item() if hasattr(value, "item") else value
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value
    if hasattr(value, "item"):
        return _safe(value.item())
    if isinstance(value, bool):
        return value
    raise ValueError(f"Unsupported result type {type(value).__name__}")


def _to_float(value: Any) -> float | None:
    output = _safe(value)
    return None if output is None else float(output)


def _str(value: Any) -> str:
    if value is None or not str(value).strip():
        raise ValueError("Missing exact contract identity")
    return str(value)


def _ensure_snapshot(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    identity = {"instrument_key", "expiry", "strike_price", "side"}
    missing = identity - set(frame.columns)
    if missing:
        raise ValueError("Missing option contract identifiers: " + ", ".join(sorted(missing)))
    if frame.duplicated(sorted(identity)).any():
        raise ValueError("Ambiguous repeated exact option contract")
    if frame["instrument_key"].astype(str).str.startswith("NSE_FO|").eq(False).any():
        raise ValueError("Only actual NSE F&O instrument identities accepted")
    if frame["side"].isin(["CE", "PE"]).eq(False).any():
        raise ValueError("Not a valid NIFTY CE/PE side")


def _to_contract(row: pd.Series) -> dict:
    # Deliberately allow-list fields: raw API payload, credentials, or user
    # identifiers never pass into the browser even if appended to input rows.
    details = {}
    for name in (
        "ltp", "spot", "bid", "ask", "gamma", "delta", "iv", "theta",
        "vega", "oi", "volume", "prev_oi", "spread_pct_mid",
        "moneyness_pct", "relative_gamma_percentile",
        "delta_shift_for_1pct_spot",
    ):
        details[name] = _to_float(row.get(name))
    is_valid_quote = bool(row.get("quote_valid", False))
    is_candidate = bool(row.get("investigate_only", False))
    reasons = []
    if not is_valid_quote:
        reasons.append("NO_VALID_BID_ASK")
    if details["gamma"] is None or details["delta"] is None:
        reasons.append("GREEKS_MISSING")
    if details["volume"] is None or details["volume"] <= 0:
        reasons.append("NO_POSITIVE_VOLUME")
    if details["oi"] is None or details["oi"] <= 0:
        reasons.append("NO_POSITIVE_OI")
    return {
        "id": {
            "instrument_key": _str(row["instrument_key"]),
            "expiry": _str(row["expiry"]),
            "strike": _to_float(row["strike_price"]),
            "side": _str(row["side"]),
        },
        "market": details,
        "quote_valid": is_valid_quote,
        "investigation_candidate": is_candidate and not reasons,
        "warnings": reasons,
    }


def build_live_gamma_panel(
    raw_chain: dict,
    *,
    retrieved_at: datetime,
    previous_chain: dict | None = None,
) -> dict:
    """Return JSON-serializable presentation data; NEVER trading signals."""
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    latest = normalize_chain(raw_chain)
    _ensure_snapshot(latest)
    last = None
    if previous_chain is not None:
        last = normalize_chain(previous_chain)
        _ensure_snapshot(last)
    contracts = [_to_contract(row) for _, row in latest.iterrows()]
    changes = []
    if last is not None and not last.empty and not latest.empty:
        comparison = compare_chain_snapshots(last, latest)
        for _, row in comparison.iterrows():
            changes.append({
                "id": {
                    "instrument_key": _str(row["instrument_key"]),
                    "expiry": _str(row["expiry"]),
                    "strike": _to_float(row["strike_price"]),
                    "side": _str(row["side"]),
                },
                "ltp_change": _to_float(row.get("ltp_change")),
                "gamma_change": _to_float(row.get("gamma_change")),
                "iv_change": _to_float(row.get("iv_change")),
                "delta_change": _to_float(row.get("delta_change")),
                "oi_change": _to_float(row.get("oi_change")),
                "volume_change": _to_float(row.get("volume_change")),
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "project": "myTrade Candle Lab",
        "panel": "GAMMA_INVESTIGATION",
        "status": "RESEARCH_ONLY",
        "order_allowed": False,
        "trade_signal": None,
        "multiplier_probability": None,
        "source": {
            "name": SOURCE,
            "underlying": "NSE_INDEX|Nifty 50",
            "retrieved_at_ist_or_offset": retrieved_at.isoformat(),
            "exchange_quote_timestamp_verified": False,
            "quote_freshness": "UNKNOWN",
            "historical_coverage": "LIVE_SNAPSHOT_ONLY",
        },
        "summary": {
            "contracts_seen": len(contracts),
            "investigation_candidates": sum(int(c["investigation_candidate"]) for c in contracts),
            "valid_bid_ask_quotes": sum(int(c["quote_valid"]) for c in contracts),
            "missing_gamma": sum(int(c["market"]["gamma"] is None) for c in contracts),
            "exact_contract_changes": len(changes),
        },
        "contracts": contracts,
        "changes": changes,
        "disclaimer": (
            "Within-expiry relative gamma/liquidity screen only. No validated 2x/3x/5x/10x "
            "probability, current exchange quote timestamp, order or executable P&L."
        ),
    }


def build_event_study_panel(
    report: dict,
    *,
    source_provenance: str,
) -> dict:
    """Keep unverified history quarantined, even if retrospective events exist.

    No source data, timestamps or raw per-contract candles go into the browser;
    only descriptive aggregate counts when provenance is approved.
    """
    if source_provenance not in {
        "UNVERIFIED", "INDEPENDENT_DAILY_ONLY", "INDEPENDENT_MINUTE_VERIFIED"
    }:
        raise ValueError("Invalid historical source provenance state")
    passed = (
        source_provenance == "INDEPENDENT_MINUTE_VERIFIED"
        and report.get("independent_source_verified") is True
    )
    keys = (
        "observed_2x_events", "observed_3x_events", "observed_5x_events", "observed_10x_events",
        "matched_controls", "unique_event_dates", "unique_event_expiries",
        "unique_event_contracts", "historical_gamma_observed_events",
    )
    if passed:
        counts = {key: int(report.get(key, 0)) for key in keys}
    else:
        # Do not surface even synthetic/unverified event counts as
        # completed real gamma findings on the primary trading UI.
        counts = {key: None for key in keys}
    return {
        "schema_version": SCHEMA_VERSION,
        "project": "myTrade Candle Lab",
        "panel": "GAMMA_EVENTS",
        "status": "DESCRIPTIVE_RESEARCH" if passed else "DATA_VERIFICATION_REQUIRED",
        "order_allowed": False,
        "trade_signal": None,
        "validated_multiplier_probability": None,
        "provenance": source_provenance,
        "minute_option_history_verified": passed,
        "counts": counts,
        "warnings": [
            "Historical independent exact-contract minute evidence is missing."
        ] if not passed else [
            "These are retrospective labels, not future trading probabilities.",
            "Historical Greeks require separately timestamped evidence.",
            "No observed multiplier is guaranteed or necessarily executable.",
        ],
    }
