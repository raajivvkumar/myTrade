"""Offline tests for immutable market data backups."""
import json
import pandas as pd
import pytest
from datetime import date
from app.data.market_archive import backup_local, verify_backup, drive_sync, sha256_file


def fixture_data(tmp_path):
    src = tmp_path / "source"
    directory = src / "NIFTY" / "1minute"
    directory.mkdir(parents=True)
    parquet = directory / "2026-03-23_to_2026-03-24_inclusive.parquet"
    pd.DataFrame({"timestamp": ["2026-03-23 09:15:00"], "open": [10],
                  "high": [12], "low": [9], "close": [11]}).to_parquet(parquet)
    provenance = parquet.with_suffix(".json")
    provenance.write_text(json.dumps({"data_kind": "INDEX_CANDLES_NOT_OPTION_CONTRACT",
        "index": "NIFTY", "interval_minutes": 1}), encoding="utf-8")
    return src, parquet, provenance


def test_copy_reopen_and_idempotence(tmp_path):
    source, parquet, _ = fixture_data(tmp_path)
    backup = tmp_path / "backup"
    assert backup_local(source, backup)["pairs"] == 1
    mirror = backup / parquet.relative_to(source)
    assert sha256_file(mirror) == sha256_file(parquet)
    assert verify_backup(backup) == 2
    assert pd.read_parquet(mirror).close.iloc[0] == 11
    assert backup_local(source, backup)["pairs"] == 1


def test_existing_different_copy_cannot_be_overwritten(tmp_path):
    source, parquet, _ = fixture_data(tmp_path)
    backup = tmp_path / "backup"
    backup_local(source, backup)
    parquet.write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs"):
        backup_local(source, backup)


def test_verify_detects_backup_tampering(tmp_path):
    source, parquet, _ = fixture_data(tmp_path)
    backup = tmp_path / "backup"
    backup_local(source, backup)
    (backup / parquet.relative_to(source)).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_backup(backup)


def test_missing_manifest_and_wrong_data_type_rejected(tmp_path):
    source, _, meta = fixture_data(tmp_path)
    meta.unlink()
    with pytest.raises(ValueError, match="provenance"):
        backup_local(source, tmp_path / "backup")
    meta.write_text(json.dumps({"data_kind": "OPTION", "index": "NIFTY",
                                "interval_minutes": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="misclassify"):
        backup_local(source, tmp_path / "backup")


def test_explicit_google_drive_and_immutable_rclone_command(monkeypatch, tmp_path):
    source, _, _ = fixture_data(tmp_path)
    backup = tmp_path / "backup"
    backup_local(source, backup)
    with pytest.raises(ValueError):
        drive_sync(backup, "unapproved:elsewhere")
    calls = []
    monkeypatch.setattr("app.data.market_archive.subprocess.run",
                        lambda args, **kwargs: calls.append(args))
    drive_sync(backup, "gdrive:MyTrade Market Data Archive/NIFTY_1m_Upstox_V3")
    assert calls[0][0:2] == ["rclone", "copy"]
    assert "--immutable" in calls[0] and "--checksum" in calls[0]


def test_absent_backup_cannot_sync(tmp_path):
    with pytest.raises(FileNotFoundError):
        drive_sync(tmp_path / "empty", "gdrive:archive")
