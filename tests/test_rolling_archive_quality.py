"""Synthetic, no-network safety tests for rolling archive quality and admission."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from app.research.rolling_archive_quality import (
    ROLLING_ONLY_STATUS, classify_rolling_frame, inspect_archive,
    require_fixed_contract_for_returns, sessions_for_day,
)


def candle(times, *, strikes=None, volumes=None, closes=None, flag=None):
    n = len(times)
    values = closes if closes is not None else [10.0] * n
    frame = pd.DataFrame({
        "timestamp": times,
        "open": values,
        "high": values,
        "low": values,
        "close": values,
        "volume": volumes if volumes is not None else [100] * n,
        "actual_strike": strikes if strikes is not None else [24000] * n,
        "spot": [24250 + i for i in range(n)],
    })
    if flag is not None:
        frame["volume_invalid_negative"] = flag
    return frame


def test_no_mutation_and_strike_switch_never_becomes_fixed_contract_pnl():
    raw = candle(
        ["2025-07-01 09:15", "2025-07-01 09:16",
         "2025-07-01 09:17", "2025-07-02 09:15"],
        strikes=[24000, 24050, 24050, 24050],
        volumes=[100, 100, 0, 100],
    )
    before = raw.copy(deep=True)
    result = classify_rolling_frame(raw, interval_minutes=1)
    pd.testing.assert_frame_equal(raw, before)
    assert result["q_strike_switched"].tolist() == [False, True, False, False]
    assert result["q_zero_volume_repeated_close"].tolist() == [
        False, False, True, False]
    assert result["proxy_eligible"].tolist() == [True, False, False, True]
    with pytest.raises(ValueError, match="Rolling"):
        require_fixed_contract_for_returns(result, independent_source_verified=True)


def test_outside_muhurat_and_exact_close_boundary_are_not_proxy_eligible():
    raw = candle(["2025-10-21 14:44", "2025-10-21 14:45",
                  "2025-10-21 14:46"], volumes=[100, 0, 0])
    out = classify_rolling_frame(raw, interval_minutes=1)
    assert out["q_outside_session"].tolist() == [False, False, True]
    assert out["q_closing_boundary"].tolist() == [False, True, False]
    assert out["proxy_eligible"].tolist() == [True, False, False]


def test_disaster_recovery_gap_and_after_close_are_nontrading():
    raw = candle(["2024-05-18 09:20", "2024-05-18 10:31",
                  "2024-05-18 11:45", "2024-05-18 12:45"])
    out = classify_rolling_frame(raw, interval_minutes=1)
    assert out["q_outside_session"].tolist() == [False, True, False, True]
    assert out["proxy_eligible"].tolist() == [True, False, True, False]


def test_extended_session_after_august_2026_does_not_reject_1530():
    df = candle(["2026-08-04 15:29", "2026-08-04 15:30",
                 "2026-08-04 15:39", "2026-08-04 15:40"])
    out = classify_rolling_frame(df, interval_minutes=1)
    assert out["q_outside_session"].sum() == 0
    assert out["q_closing_boundary"].tolist() == [False, False, False, True]
    assert out["proxy_eligible"].tolist() == [True, True, True, False]


def test_zero_volume_nonflat_and_missing_source_volume_not_fabricated():
    df = candle(["2025-09-02 09:15", "2025-09-02 09:20",
                 "2025-09-02 09:25"], volumes=[100, 0, float("nan")],
                closes=[10, 11, 12], flag=[False, False, True])
    df.loc[1, ["open", "high", "low"]] = [10, 12, 10]
    out = classify_rolling_frame(df, interval_minutes=5)
    assert out["q_zero_volume"].tolist() == [False, True, False]
    assert out["q_zero_volume_nonflat"].tolist() == [False, True, False]
    assert out["q_source_negative_volume"].tolist() == [False, False, True]
    assert out["proxy_eligible"].tolist() == [True, False, False]
    assert pd.isna(out.loc[2, "volume"])


def test_5m_alignment_and_bad_high_low_rejected():
    df = candle(["2025-01-02 09:16"])
    with pytest.raises(ValueError, match="align"):
        classify_rolling_frame(df, interval_minutes=5)
    df.loc[0, "high"] = 1.0
    with pytest.raises(ValueError, match="bounds"):
        classify_rolling_frame(df, interval_minutes=1)


def test_custom_closures_and_override_precedence():
    day = date(2025, 1, 2)
    assert sessions_for_day(day, closed_dates={day}) == ()
    assert sessions_for_day(day, closed_dates={day},
                            overrides={day: (("10:00", "11:00"),)}) == (
                                ("10:00", "11:00"),)
    assert sessions_for_day(date(2026, 2, 1)) == (("09:15", "15:30"),)
    assert sessions_for_day(date(2026, 8, 2)) == ()


def test_exact_contract_attestation_still_requires_stable_identity():
    df = candle(["2025-01-02 09:15", "2025-01-02 09:16"])
    df = df.drop(columns=["actual_strike"])
    df["instrument_key"] = ["NSE_FO|123", "NSE_FO|123"]
    df["expiry"] = "2025-01-30"
    df["strike_price"] = 24000
    df["option_type"] = "CALL"
    with pytest.raises(ValueError, match="Independent"):
        require_fixed_contract_for_returns(df)
    assert require_fixed_contract_for_returns(
        df, independent_source_verified=True) is True
    df.loc[1, "instrument_key"] = "NSE_FO|456"
    with pytest.raises(ValueError, match="unchanging"):
        require_fixed_contract_for_returns(
            df, independent_source_verified=True)


def test_zip_scan_readonly_with_manifest_and_csv_row_checks(tmp_path: Path):
    path = tmp_path / "NIFTY_DHAN_2025Q4_rolling_1m_5m.zip"
    raw = candle(["2025-10-21 14:44", "2025-10-21 14:45",
                  "2025-10-21 14:46"], volumes=[10, 0, 0])
    name = "1m/WEEK_1_ATM_CALL/20251001_20251031.csv"
    meta_name = "meta/" + name[:-4] + ".json"
    manifest = {
        "quarter": "2025Q4", "source": "DhanHQ v2 /charts/rollingoption",
        "fixed_option_contract_expiry_verified": False,
        "requests_with_successful_responses": 1,
        "empty_responses": 0, "rows_exported": 3,
    }
    meta = {"status": "ROWS", "csv": name, "rows": 3}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr(name, raw.to_csv(index=False))
        z.writestr(meta_name, json.dumps(meta))
    before = path.read_bytes()
    report = inspect_archive(path)
    assert path.read_bytes() == before
    assert report["status"] == ROLLING_ONLY_STATUS
    assert report["rows"] == 3 and report["orders"] == 0
    assert report["flags"]["q_closing_boundary"] == 1
    assert report["flags"]["q_outside_session"] == 1
    assert report["flags"]["proxy_eligible_rows"] == 1
    assert report["fixed_contract_returns_enabled"] is False
