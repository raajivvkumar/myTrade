"""Offline NIFTY 1-minute quality audit tests; never contact broker."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.data.index_quality import audit_index


def sample_archive(tmp_path: Path, *, missing_minute=None):
    root = tmp_path / "archive"
    folder = root / "NIFTY" / "1minute"
    folder.mkdir(parents=True)
    stamps = pd.date_range("2026-03-23 09:15", periods=375, freq="min")
    if missing_minute is not None:
        stamps = stamps.delete(missing_minute)
    frame = pd.DataFrame({
        "timestamp": stamps,
        "open": [23000.0] * len(stamps),
        "high": [23001.0] * len(stamps),
        "low": [22999.0] * len(stamps),
        "close": [23000.5] * len(stamps),
        "volume": [0] * len(stamps), "oi": [0] * len(stamps),
    })
    dest = folder / "2026-03-23_to_2026-03-23_inclusive.parquet"
    frame.to_parquet(dest)
    meta = {
        "source": "Upstox Historical Candle Data V3",
        "data_kind": "INDEX_CANDLES_NOT_OPTION_CONTRACT",
        "index": "NIFTY", "interval_minutes": 1,
        "rows": len(frame),
        "first_timestamp_ist": str(stamps.min()),
        "last_timestamp_ist": str(stamps.max()),
    }
    dest.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    return root, frame, dest


def test_full_day_has_375_without_missing_and_no_external_validation(tmp_path):
    root, _, _ = sample_archive(tmp_path)
    report, days = audit_index(root)
    assert report["unique_rows"] == 375
    assert report["observed_regular_session_missing_minutes"] == 0
    assert report["reference_comparison"] == "NOT_PERFORMED"
    assert report["quality_status"] == "UNVALIDATED_NEEDS_INDEPENDENT_NSE_CHECK"
    assert days[0]["expected_regular_session_minutes"] == 375


def test_a_missing_one_minute_candle_is_reported_not_synthesized(tmp_path):
    root, _, _ = sample_archive(tmp_path, missing_minute=10)
    report, days = audit_index(root)
    assert report["unique_rows"] == 374
    assert report["observed_regular_session_missing_minutes"] == 1
    assert "09:25:00" in report["examples_missing_ist"][0]
    assert days[0]["missing_regular_session_minutes"] == 1


def test_duplicate_same_timestamp_same_data_is_identified(tmp_path):
    root, original, dest = sample_archive(tmp_path)
    duplicate = original.iloc[[0]].copy()
    combined = pd.concat([original, duplicate], ignore_index=True)
    combined.to_parquet(dest)
    meta = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    meta["rows"] = len(combined)
    dest.with_suffix(".json").write_text(json.dumps(meta))
    result, _ = audit_index(root)
    assert result["unique_rows"] == 375
    assert result["duplicate_rows_identical"] == 1


def test_conflicting_candle_is_not_silently_trusted(tmp_path):
    root, original, dest = sample_archive(tmp_path)
    duplicate = original.iloc[[0]].copy()
    duplicate["close"] = 23000.9
    combined = pd.concat([original, duplicate], ignore_index=True)
    combined.to_parquet(dest)
    meta = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    meta["rows"] = len(combined)
    dest.with_suffix(".json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="Conflicting"):
        audit_index(root)


def test_external_reference_comparison_does_not_claim_nse_authenticity(tmp_path):
    root, original, _ = sample_archive(tmp_path)
    external = original[["timestamp", "open", "high", "low", "close"]].copy()
    external.loc[0, "close"] = 23004
    reference = tmp_path / "reference.csv"
    external.to_csv(reference, index=False)
    report, _ = audit_index(root, reference_csv=reference, point_tolerance=0.5)
    assert report["reference_mismatched_minutes"] == 1
    assert report["reference_overlapping_minutes"] == 375
    assert report["reference_comparison"] == "USER_SUPPLIED_REFERENCE_COMPARED_NOT_NSE_CERTIFIED"


def test_metadata_row_count_fails_closed(tmp_path):
    root, _, dest = sample_archive(tmp_path)
    m = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    m["rows"] = 10
    dest.with_suffix(".json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="row count"):
        audit_index(root)


def test_manifest_required_and_mismatched_instrument_rejected(tmp_path):
    root, _, dest = sample_archive(tmp_path)
    meta = dest.with_suffix(".json")
    meta.unlink()
    with pytest.raises(ValueError, match="manifest"):
        audit_index(root)
    meta.write_text(json.dumps({"data_kind": "OPTION", "index": "NIFTY",
                                "interval_minutes": 1, "rows": 375}))
    with pytest.raises(ValueError, match="Wrong instrument"):
        audit_index(root)


def test_out_of_session_is_reported_not_silently_deleted(tmp_path):
    root, original, dest = sample_archive(tmp_path)
    extra = original.iloc[[0]].copy()
    extra.loc[:, "timestamp"] = pd.Timestamp("2026-03-23 15:30")
    combined = pd.concat([original, extra], ignore_index=True)
    combined.to_parquet(dest)
    meta = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    meta.update(rows=len(combined), last_timestamp_ist="2026-03-23 15:30:00")
    dest.with_suffix(".json").write_text(json.dumps(meta))
    result, _ = audit_index(root)
    assert result["outside_regular_session_minutes"] == 1
    assert result["unique_rows"] == 376


def test_no_files_is_fatal_not_a_pass(tmp_path):
    (tmp_path / "archive").mkdir()
    with pytest.raises(ValueError, match="No 1-minute"):
        audit_index(tmp_path / "archive")


def test_daily_reference_ohl_matches_but_official_close_can_differ(tmp_path):
    root, _, _ = sample_archive(tmp_path)
    daily_csv = tmp_path / "daily.csv"
    daily_csv.write_text(
        "date,open,high,low,close\n"
        "2026-03-23,23000,23001,22999,23000.25\n",
        encoding="utf-8",
    )
    report, days = audit_index(root, daily_reference_csv=daily_csv)
    assert report["daily_reference_overlapping_days"] == 1
    assert report["daily_reference_ohl_mismatch_days"] == 0
    assert report["quality_status"] == "UNVALIDATED_NEEDS_INDEPENDENT_NSE_CHECK"
    assert days[0]["reference_daily_close"] == 23000.25
    assert days[0]["reference_close_minus_last_minute_points"] == -0.25
    assert days[0]["ohl_matches_user_daily_reference"] is True
    assert days[0]["close_definition"] == "LAST_1MIN_BAR_NOT_OFFICIAL_NIFTY_DAILY_CLOSE"


def test_daily_reference_ohl_disagreement_is_flagged_not_auto_repaired(tmp_path):
    root, _, _ = sample_archive(tmp_path)
    daily_csv = tmp_path / "daily.csv"
    daily_csv.write_text(
        "date,open,high,low,close\n"
        "2026-03-23,23005,23010,22999,23004\n",
        encoding="utf-8",
    )
    report, days = audit_index(root, daily_reference_csv=daily_csv)
    assert report["daily_reference_ohl_mismatch_days"] == 1
    assert days[0]["ohl_matches_user_daily_reference"] is False


def test_duplicate_daily_reference_dates_are_rejected(tmp_path):
    root, _, _ = sample_archive(tmp_path)
    daily_csv = tmp_path / "daily.csv"
    daily_csv.write_text(
        "date,open,high,low,close\n"
        "2026-03-23,23000,23001,22999,23000\n"
        "2026-03-23,23000,23001,22999,23000\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Daily reference"):
        audit_index(root, daily_reference_csv=daily_csv)
