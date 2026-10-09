"""Offline tests for bulk index-versus-fixed-option history gate."""
from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from app.research.gamma_bulk_scan_cli import scan_directory, _schema


def minutes(*, key="NSE_FO|TEST230", peak=10.0, date="2026-10-07"):
    t = pd.date_range(f"{date} 09:15:00", periods=230, freq="min")
    result = pd.DataFrame({
        "timestamp": t, "open": 4.0, "high": 5.0, "low": 3.0,
        "close": 4.0, "volume": 100.0, "oi": 1200.0,
        "instrument_key": key, "strike_price": 25000,
        "option_type": "PE", "expiry": "2026-10-13",
        "symbol": "NIFTY 2026-10-13 25000 PE",
    })
    if peak is not None:
        result.loc[135, "close"] = peak
        result.loc[135, "high"] = peak + 1.0
    return result


def test_tiny_premium_threshold_is_not_confused_with_2x_volume(tmp_path):
    sample = minutes(peak=10.0)
    sample.loc[:, ["open", "high", "low", "close"]] *= 0.01
    sample.to_csv(tmp_path / "option.csv", index=False)
    report = scan_directory(tmp_path, min_premium=2.0)
    assert report["eligible_start_minutes"] == 0
    assert report["observed_2x_events"] == 0
    assert report["status"].startswith("EXPLORATORY_")
    assert report["external_network_requests"] == 0
    assert report["orders"] == 0


def test_exact_option_2x_only_but_no_3x_and_control_under_2(tmp_path):
    minutes().to_csv(tmp_path / "contract.csv", index=False)
    snapshot = (tmp_path / "contract.csv").read_bytes()
    report = scan_directory(tmp_path)
    assert report["exact_contract_minute_files_accepted_structurally"] == 1
    assert report["observed_2x_events"] == 1
    assert report["observed_3x_events"] == 0
    assert report["observed_5x_events"] == 0
    assert report["observed_10x_events"] == 0
    assert report["matched_below_2x_controls"] == 1
    assert report["unique_event_dates"] == 1
    assert "volume_surge_2x" in report["candidate_condition_comparison"]
    assert (tmp_path / "contract.csv").read_bytes() == snapshot
    assert list(tmp_path.iterdir()) == [tmp_path / "contract.csv"]


def test_legacy_tick_and_daily_ohlc_never_reclassified_as_minute_events(tmp_path):
    legacy = "date,time,price,volume,oi\n2024-04-03,09:15:00,0.05,50,2000\n"
    (tmp_path / "tick.csv").write_text(legacy, encoding="utf-8")
    daily = "INSTRUMENT,SYMBOL,OPEN,HIGH,LOW,CLOSE,EXPIRY_DT\nOPTIDX,NIFTY,0.1,1,0.05,0.5,28-Jan-2016\n"
    (tmp_path / "daily.csv").write_text(daily, encoding="utf-8")
    report = scan_directory(tmp_path)
    assert report["scanned_files"] == 2
    assert report["file_status_counts"]["TICK_SCHEMA_QUARANTINED_NOT_OHLC"] == 1
    assert report["file_status_counts"]["OTHER_OHLC_NO_EXACT_MINUTE_CONTRACT"] == 1
    assert report["observed_2x_events"] == 0


def test_nested_zip_daily_csv_stays_unverified_and_rar_counted(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("daily.csv", "OPEN,HIGH,LOW,CLOSE\n1,10,1,1\n")
    with zipfile.ZipFile(tmp_path / "outer.zip", "w") as z:
        z.writestr("inner.zip", inner.getvalue())
        z.writestr("raw_archive.rar", b"not-an-extractable-test")
    report = scan_directory(tmp_path)
    assert report["scanned_files"] == 1
    assert report["observed_10x_events"] == 0
    assert report["file_status_counts"]["OTHER_OHLC_NO_EXACT_MINUTE_CONTRACT"] == 1
    assert report["unscanned_container_types"]["RAR_OR_7Z_REQUIRES_LOCAL_EXTRACTION"] == 1


def test_source_exact_duplicates_not_double_counted(tmp_path):
    raw = minutes().to_csv(index=False)
    (tmp_path / "a.csv").write_text(raw)
    (tmp_path / "b.csv").write_text(raw)
    report = scan_directory(tmp_path)
    assert report["file_status_counts"]["DUPLICATE_SOURCE_BYTES_SKIPPED"] == 1
    assert report["observed_2x_events"] == 1


def test_same_contract_day_overlap_is_rejected_for_safety(tmp_path):
    a = minutes()
    b = minutes().copy()
    b.loc[:, "volume"] = 20
    a.to_csv(tmp_path / "a.csv", index=False)
    b.to_csv(tmp_path / "b.csv", index=False)
    result = scan_directory(tmp_path)
    assert result["exact_contract_minute_files_accepted_structurally"] == 1
    assert result["file_status_counts"]["MINUTE_SCHEMA_REJECTED"] == 1
    assert result["observed_2x_events"] == 1


def test_nonexistent_directory_and_bad_horizon_raise(tmp_path):
    with pytest.raises(ValueError, match="existing"):
        scan_directory(tmp_path / "absent")
    with pytest.raises(ValueError, match="Horizon"):
        scan_directory(tmp_path, horizon=121)


def test_report_is_strict_json_serializable(tmp_path):
    minutes().to_csv(tmp_path / "sample.csv", index=False)
    report = scan_directory(tmp_path)
    json.dumps(report, allow_nan=False)
