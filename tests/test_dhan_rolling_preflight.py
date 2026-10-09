"""Synthetic Dhan rolling-cache safety checks; no broker or real market data."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.research.dhan_rolling_preflight import audit_rolling_frame, inspect_dhan_cache


def sample(strikes=(25000, 25000, 25050, 25050)):
    ts = pd.date_range("2026-10-08 09:15:00", periods=len(strikes), freq="min")
    return pd.DataFrame({
        "timestamp": ts, "series": "WEEK_1_ATM_CALL",
        "actual_strike": strikes, "open": 10.0, "high": 12.0,
        "low": 9.0, "close": 11.0, "volume": 5.0, "oi": 20.0,
        "iv": [0.2] * len(strikes),
    })


def test_rolling_switch_is_counted_not_promoted():
    result = audit_rolling_frame(sample(), "WEEK_1_ATM_CALL")
    assert result["rows"] == 4
    assert result["strike_switches"] == 1
    assert result["fixed_contract_eligible"] is False


def test_same_strike_plus_untrusted_expiry_still_blocked():
    frame = sample((25000,) * 4)
    frame["expiry"] = "2026-10-13"
    frame["instrument_key"] = "NSE_FO|CLAIMED"
    result = audit_rolling_frame(frame, "WEEK_1_ATM_CALL")
    assert result["strike_switches"] == 0
    assert result["fixed_contract_eligible"] is False


def test_bad_schema_and_duplicate_times_rejected():
    with pytest.raises(ValueError, match="Missing"):
        audit_rolling_frame(sample().drop(columns=["actual_strike"]), "WEEK_1_ATM_CALL")
    dup = sample()
    dup.loc[1, "timestamp"] = dup.loc[0, "timestamp"]
    with pytest.raises(ValueError, match="Duplicate"):
        audit_rolling_frame(dup, "WEEK_1_ATM_CALL")


def test_missing_iv_counts_as_missing_not_zero():
    frame = sample()
    frame["iv"] = [float("nan")] * len(frame)
    assert audit_rolling_frame(frame, "WEEK_1_ATM_CALL")["optional_missing"]["iv"] == 4


def test_cli_preflight_never_claims_gamma(monkeypatch, tmp_path: Path):
    folder = tmp_path / "chunks" / "WEEK_1_ATM_CALL"
    folder.mkdir(parents=True)
    (folder / "test.parquet").touch()
    monkeypatch.setattr(pd, "read_parquet", lambda _: sample())
    report = inspect_dhan_cache(tmp_path)
    assert report["status"] == "BLOCKED_FIXED_CONTRACT_EXPIRY_UNVERIFIED"
    assert report["chunks_checked"] == 1
    assert report["strike_switches_within_sessions"] == 1
    assert report["eligible_fixed_contract_windows"] == 0
    assert report["validated_5x_events"] is None
    assert report["prediction_accuracy"] is None
