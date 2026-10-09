"""Offline integrity verification of NIFTY active-option local and mirror archives.

NEVER contacts the broker. Work remains possible after the option has expired.
No deletion, overwrite, migration, upload or user credentials.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from app.broker.upstox_active_option_archive import target_path
from app.data.market_archive import sha256_file


def verify_active_archive(primary: Path, mirror: Path) -> dict:
    primary, mirror = Path(primary).resolve(), Path(mirror).resolve()
    if primary == mirror or primary in mirror.parents or mirror in primary.parents:
        raise ValueError("Independent mirror must be outside the primary archive tree")
    if not primary.is_dir() or not mirror.is_dir():
        raise FileNotFoundError("Both primary and offline mirror must exist")
    examined = 0
    for parquet in sorted(primary.rglob("*.parquet")):
        if parquet.is_symlink():
            raise ValueError("Cannot verify symlinked historical Parquet")
        meta = parquet.with_suffix(".json")
        if not meta.is_file() or meta.is_symlink():
            raise ValueError(f"Missing exact-contract source manifest: {parquet.name}")
        relative = parquet.relative_to(primary)
        twin = mirror / relative
        twin_manifest = twin.with_suffix(".json")
        if any(not x.is_file() or x.is_symlink() for x in (twin, twin_manifest)):
            raise ValueError("Missing or symlinked independent offline mirror file")
        record = json.loads(meta.read_text(encoding="utf-8"))
        if record.get("schema") != "MYTRADE_NIFTY_ACTIVE_OPTION_1M_V1" or record.get(
            "data_kind"
        ) != "EXACT_CONTRACT_OPTION_CANDLES_UNVERIFIED":
            raise ValueError("Wrong or altered archived contract provenance")
        from datetime import date
        actual_day = date.fromisoformat(record["day_ist"])
        canonical = target_path(primary, record["contract"], actual_day)
        if canonical != parquet:
            raise ValueError("Parquet path does not match exact contract/expiry/day")
        first_sha = sha256_file(parquet)
        if first_sha != record["parquet_sha256"] or sha256_file(twin) != first_sha:
            raise ValueError("Option archive Parquet SHA256 mismatch")
        if sha256_file(twin_manifest) != sha256_file(meta):
            raise ValueError("Option archive manifest SHA256 mismatch")
        # Hash checks alone cannot prove independent exchange provenance.
        frame = pd.read_parquet(parquet, columns=[
            "timestamp", "instrument_key", "option_type", "expiry",
        ])
        if frame.empty or frame.instrument_key.ne(record["contract"]["instrument_key"]).any():
            raise ValueError("Stored Parquet has invalid contract identity")
        if frame.option_type.ne(record["contract"]["option_type"]).any():
            raise ValueError("Stored Parquet changes option side")
        if frame.expiry.ne(record["contract"]["expiry"]).any():
            raise ValueError("Stored Parquet changes expiry identity")
        if pd.to_datetime(frame.timestamp).dt.date.ne(actual_day).any():
            raise ValueError("Stored Parquet changes trading day")
        examined += 1
    if examined == 0:
        raise ValueError("No historical option archive pairs found")
    return {
        "status": "LOCAL_SHA256_MIRROR_VERIFIED_NOT_EXCHANGE_AUTHENTICATED",
        "pairs": examined,
        "token_required": False,
        "network_requests": 0,
        "market_data_verified_against_exchange": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify NIFTY exact-contract offline archive SHA256, no broker login"
    )
    parser.add_argument("--source-dir", type=Path, default=Path("data/raw/upstox/options_unvalidated"))
    parser.add_argument("--backup-dir", type=Path, default=Path("../MyTradeOfflineArchive/upstox_options_v3"))
    args = parser.parse_args()
    result = verify_active_archive(args.source_dir, args.backup_dir)
    print(f"Verified {result['pairs']} NIFTY option 1-minute source/mirror pairs offline.")
    print(result["status"])


if __name__ == "__main__":
    main()
