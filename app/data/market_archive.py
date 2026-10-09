"""Immutable offline backup of NIFTY index 1-minute data.

An optional rclone destination can be a private Google Drive folder. No Upstox
credentials, Upstox login or broker network are required for backup/restoration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

SOURCE = Path("data/raw/upstox/index_unvalidated")
BACKUP = Path("../MyTradeOfflineArchive/upstox_index_v3")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def immutable_copy(source: Path, target: Path) -> str:
    checksum = sha256_file(source)
    if target.exists():
        if sha256_file(target) != checksum:
            raise ValueError(f"Existing backup differs, refusing overwrite: {target}")
        return checksum
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".partial")
    if temp.exists():
        raise FileExistsError(f"Inspect/remove incomplete prior backup: {temp}")
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if sha256_file(temp) != checksum:
            raise IOError("Backup hash mismatch during copy")
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()
    return checksum


def backup_local(source: Path = SOURCE, backup: Path = BACKUP) -> dict:
    source, backup = Path(source), Path(backup)
    if not source.exists():
        return {"pairs": 0, "files": [], "backup": str(backup)}
    if not source.is_dir():
        raise NotADirectoryError(source)
    if backup.resolve() == source.resolve() or source.resolve() in backup.resolve().parents:
        raise ValueError("Backup destination must be outside the source tree")
    catalogue = backup / "archive_catalogue.json"
    old_files = {}
    if catalogue.exists():
        previous = json.loads(catalogue.read_text(encoding="utf-8"))
        if previous.get("schema") != "MYTRADE_INDEX_BACKUP_V1":
            raise ValueError("Unknown prior backup catalogue schema")
        for entry in previous["files"]:
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe backup catalogue path")
            path = backup / relative
            if not path.is_file() or sha256_file(path) != entry["sha256"]:
                raise ValueError(f"Previous backup missing or modified: {path}")
            old_files[entry["path"]] = entry
    count = 0
    for file in sorted(source.rglob("*.parquet")):
        if not file.is_file() or file.is_symlink():
            raise ValueError("Symlinked or invalid source file")
        metadata = file.with_suffix(".json")
        if not metadata.is_file() or metadata.is_symlink():
            raise ValueError(f"Missing provenance JSON for {file}")
        provenance = json.loads(metadata.read_text(encoding="utf-8"))
        if provenance.get("data_kind") != "INDEX_CANDLES_NOT_OPTION_CONTRACT":
            raise ValueError("Cannot misclassify expired option or unknown data as index")
        if provenance.get("index") != "NIFTY" or provenance.get("interval_minutes") != 1:
            raise ValueError("This backup supports NIFTY index 1-minute history only")
        for original in (file, metadata):
            rel = original.relative_to(source).as_posix()
            sha = immutable_copy(original, backup / rel)
            old_files[rel] = {"path": rel, "sha256": sha, "bytes": original.stat().st_size}
        count += 1
    if count or catalogue.exists():
        backup.mkdir(parents=True, exist_ok=True)
        catalog = {"schema": "MYTRADE_INDEX_BACKUP_V1",
                   "data_kind": "NIFTY_1MIN_INDEX_NOT_OPTIONS",
                   "quality": "UNVALIDATED_NEEDS_NSE_CROSSCHECK",
                   "files": [old_files[key] for key in sorted(old_files)]}
        staging = catalogue.with_suffix(".partial")
        staging.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        os.replace(staging, catalogue)
    return {"pairs": count, "files": list(old_files.values()), "backup": str(backup)}


def verify_backup(backup: Path = BACKUP) -> int:
    backup = Path(backup)
    data = json.loads((backup / "archive_catalogue.json").read_text(encoding="utf-8"))
    if data.get("schema") != "MYTRADE_INDEX_BACKUP_V1":
        raise ValueError("Unsupported backup catalogue")
    for item in data["files"]:
        rel = Path(item["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Unsafe backup path")
        candidate = backup / rel
        if not candidate.is_file() or sha256_file(candidate) != item["sha256"]:
            raise ValueError(f"Backup hash mismatch: {candidate}")
    return len(data["files"])


def drive_sync(backup: Path, remote: str) -> None:
    if not remote.startswith("gdrive:") or remote == "gdrive:":
        raise ValueError("Require explicit gdrive: folder destination")
    if verify_backup(backup) < 1:
        raise ValueError("Empty archive")
    cmd = ["rclone", "copy", str(backup), remote, "--immutable", "--checksum",
           "--transfers", "2", "--include", "*.parquet", "--include", "*.json"]
    try:
        subprocess.run(cmd, check=True, timeout=1800)
    except FileNotFoundError as exc:
        raise RuntimeError("rclone not installed; use Drive web upload instead") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Google Drive sync failed. Local backup remains safe.") from exc


def main():
    p = argparse.ArgumentParser(description="Broker-independent immutable archive")
    p.add_argument("action", choices=("backup", "verify", "drive-sync"))
    p.add_argument("--source", type=Path, default=SOURCE)
    p.add_argument("--backup-dir", type=Path, default=BACKUP)
    p.add_argument("--rclone-remote", help="e.g. gdrive:MyTrade Market Data Archive/NIFTY_1m_Upstox_V3")
    a = p.parse_args()
    if a.action == "backup":
        result = backup_local(a.source, a.backup_dir)
        print(f"Copied/verified {result['pairs']} candle files; location: {a.backup_dir}")
    elif a.action == "verify":
        print(f"Verified {verify_backup(a.backup_dir)} offline files (SHA256)")
    else:
        if not a.rclone_remote:
            p.error("drive-sync needs --rclone-remote")
        drive_sync(a.backup_dir, a.rclone_remote)
        print("Google Drive upload completed (existing remote files retained)")


if __name__ == "__main__":
    main()
