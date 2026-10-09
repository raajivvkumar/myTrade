"""Quarterly NIFTY expired rolling-options archive: Dhan -> ZIP -> Google Drive.

One ZIP per calendar quarter containing 1m and 5m CSVs, request metadata
and coverage manifest. Requires local Dhan token and an independently
authenticated rclone Google Drive remote. No order/trading API is used.

Resume uses ZIP metadata journals; successfully uploaded, MD5-verified ZIPs
may have their local staging copy removed. Vendor retention remains five
years, so Jan-Sep 2021 cannot be backfilled on 2026-10-09 via this API.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile
from zoneinfo import ZoneInfo

from app.broker.dhan_cli import load_local_credentials
from app.broker.dhan_historical import DhanClient, DhanAPIError
from app.broker.dhan_history import parse_bars, five_year_start
from app.research.dhan_direct_ram_study import queries

DRIVE_FOLDER_ID = "12XxOYya_1umSUvbcR3haOKXsVH6muZ63"
IST = ZoneInfo("Asia/Kolkata")
QUARTER_RE = re.compile(r"^20[0-9]{2}Q[1-4]$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9_-]+:$")
INTERVALS = (1, 5)


@dataclass(frozen=True)
class Quarter:
    key: str
    quarter_start: date
    quarter_end_exclusive: date
    eligible_from: date | None
    eligible_end_exclusive: date | None
    status: str
    zip_name: str | None


def _quarter_floor(d):
    return date(d.year, 1 + 3 * ((d.month - 1) // 3), 1)


def _quarter_next(start):
    month = start.month + 3
    return date(start.year + (month > 12), month if month <= 12 else month - 12, 1)


def plan_quarters(start, through, *, today):
    """Plan by calendar quarter; never pretend data predating vendor retention exists."""
    if not start <= through <= today:
        raise ValueError("Historical period must satisfy start <= through <= today")
    vendor_floor = five_year_start(today)
    end_exclusive = through + timedelta(days=1)
    cursor = _quarter_floor(start)
    result = []
    while cursor < end_exclusive:
        nxt = _quarter_next(cursor)
        nominal_start = max(cursor, start)
        nominal_end = min(nxt, end_exclusive)
        usable_start = max(nominal_start, vendor_floor)
        usable_end = nominal_end if usable_start < nominal_end else None
        key = f"{cursor.year}Q{((cursor.month - 1) // 3) + 1}"
        if usable_end is None:
            status = "UNAVAILABLE_BEFORE_DHAN_FIVE_YEAR_RETENTION"
            filename = None
            valid_start = None
        else:
            valid_start = usable_start
            if usable_start > nominal_start:
                status = "PARTIAL_EARLY_RETENTION"
            elif nominal_end < nxt:
                status = "PARTIAL_QUARTER_UNTIL_REQUESTED_DATE"
            else:
                status = "REQUESTED_QUARTER"
            suffix = (f"_through_{(usable_end - timedelta(days=1)):%Y%m%d}"
                      if nominal_end < nxt else "")
            filename = f"NIFTY_DHAN_{key}_rolling_1m_5m{suffix}.zip"
        result.append(Quarter(
            key, cursor, nxt, valid_start, usable_end, status, filename))
        cursor = nxt
    return result


def _rclone(cmd, *, capture=True):
    if shutil.which("rclone") is None:
        raise RuntimeError("rclone is not installed or not on PATH")
    completed = subprocess.run(["rclone", *cmd], check=False,
                               capture_output=capture, text=True, timeout=1800)
    if completed.returncode:
        # Avoid printing OAuth tokens, provider raw response text or CLI config.
        raise RuntimeError(
            f"rclone command {cmd[0]} failed (exit {completed.returncode}); "
            "check your local rclone authentication and Drive permissions")
    return completed.stdout.strip() if capture else ""


def _verify_remote(remote):
    if not REMOTE_RE.fullmatch(remote):
        raise ValueError("Remote must look like 'dhanarchive:' (no path)")
    names = set(_rclone(["listremotes"]).splitlines())
    if remote not in names:
        raise RuntimeError("Configured rclone remote was not found; run rclone config")
    # The remote must be rooted to DRIVE_FOLDER_ID by the user's local rclone
    # configuration. We cannot read or export its OAuth credentials here.
    _rclone(["lsf", remote, "--files-only", "--max-depth", "1"])


def _md5_file(path):
    digest = hashlib.md5()  # checksum comparison only, not a security primitive
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _remote_md5(remote, name):
    lines = _rclone(["md5sum", remote + name]).splitlines()
    if len(lines) != 1 or not re.match(r"^[0-9a-fA-F]{32}\s", lines[0]):
        raise RuntimeError("Google Drive did not return a verifiable MD5")
    return lines[0].split()[0].lower()


def _remote_names(remote):
    return set(_rclone(["lsf", remote, "--files-only", "--max-depth", "1"]).splitlines())


def upload_verified(local_zip, remote, *, existing=None):
    existing = _remote_names(remote) if existing is None else existing
    final_name = local_zip.name
    source_md5 = _md5_file(local_zip)
    if final_name in existing:
        if _remote_md5(remote, final_name) != source_md5:
            raise RuntimeError(
                "Refusing to overwrite a different Google Drive quarterly archive")
        return "ALREADY_PRESENT_MD5_VERIFIED"
    _rclone(["copyto", str(local_zip), remote + final_name,
             "--checksum", "--retries", "3"], capture=True)
    if _remote_md5(remote, final_name) != source_md5:
        raise RuntimeError("Google Drive MD5 verification failed; local ZIP retained")
    return "UPLOADED_MD5_VERIFIED"


def _manifest_meta_path(interval, series, window_start, window_end):
    return (f"meta/{interval}m/{series}/"
            f"{window_start:%Y%m%d}_{window_end:%Y%m%d}.json")


def _csv_member(interval, series, window_start, window_end):
    return (f"{interval}m/{series}/"
            f"{window_start:%Y%m%d}_{window_end:%Y%m%d}.csv")


def _validated_frame(raw, q, series):
    entry = {"series": series, "start": q.start.isoformat(),
             "end": q.end.isoformat(), "payload": q.payload()}
    return parse_bars(raw, entry)


def _load_completed(stage):
    if not stage.exists():
        return {}
    output = {}
    with zipfile.ZipFile(stage, "r") as archive:
        names = set(archive.namelist())
        for name in names:
            if name.startswith("meta/") and name.endswith(".json"):
                record = json.loads(archive.read(name))
                if record.get("status") == "ROWS":
                    csv_name = record.get("csv")
                    if not csv_name or csv_name not in names:
                        raise RuntimeError("Partial archive has metadata without CSV")
                output[name] = record
        for name in names:
            if name.endswith(".csv"):
                meta_name = "meta/" + name[:-4] + ".json"
                if meta_name not in output:
                    raise RuntimeError("Partial archive has an unjournaled CSV; inspect safely")
    return output


def _each_query(q, intervals):
    for base in queries(q.eligible_from, q.eligible_end_exclusive - timedelta(days=1),
                        full=True):
        for interval in intervals:
            yield replace(base, interval=interval)


def _get_broker_frame(client, request, series, sleeper, delay):
    # Retry only transient rate-limit/provider failures. 401/403 must be
    # resolved by refreshing local credentials, not by indefinite retries.
    for attempt in range(4):
        sleeper(delay if attempt == 0 else min(30, 2 ** (attempt + 1)))
        try:
            raw = client._call("POST", "/charts/rollingoption", request.payload())
        except DhanAPIError as exc:
            if exc.http_status not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            continue
        return _validated_frame(raw, request, series)
    raise RuntimeError("Retry policy exhausted")


def archive_quarter(q, *, client, staging, remote, intervals=INTERVALS,
                    pause=.35, sleeper=time.sleep, upload_fn=upload_verified,
                    progress=False):
    """Resume a quarter locally, upload one ZIP, then delete only its staging file.

    The staging .partial is retained on API or upload failures to avoid
    re-downloading all previous successful request members.
    """
    if q.eligible_from is None or q.zip_name is None:
        return {"quarter": q.key, "status": q.status, "calls": 0}
    staging.mkdir(parents=True, exist_ok=True)
    part_path = staging / (q.zip_name + ".partial")
    final_path = staging / q.zip_name
    if final_path.exists() and part_path.exists():
        raise RuntimeError("Found final ZIP and partial ZIP for same quarter")
    if final_path.exists():
        # A completed validated ZIP can be retried after an upload failure.
        outcome = upload_fn(final_path, remote)
        final_path.unlink()
        return {"quarter": q.key, "status": outcome, "resumed_completed_zip": True}
    complete = _load_completed(part_path)
    planned = list(_each_query(q, intervals))
    performed = 0
    with zipfile.ZipFile(part_path, "a", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as archive:
        for number, request in enumerate(planned, start=1):
            series = (f"{request.expiry_flag}_{request.expiry_code}_"
                      f"{request.strike}_{request.side}")
            metadata_name = _manifest_meta_path(request.interval, series,
                                                 request.start, request.end)
            if metadata_name in complete:
                continue
            frame = _get_broker_frame(client, request, series, sleeper, pause)
            csv_name = _csv_member(request.interval, series,
                                   request.start, request.end)
            record = {
                "status": "EMPTY" if frame.empty else "ROWS",
                "interval_minutes": request.interval,
                "series": series, "relative_strike": request.strike,
                "side": request.side, "expiry_flag": request.expiry_flag,
                "expiry_code": request.expiry_code,
                "window_from_inclusive": request.start.isoformat(),
                "window_to_exclusive": request.end.isoformat(),
                "rows": int(len(frame)), "csv": csv_name if not frame.empty else None,
                "first_observed_ist": (str(frame.timestamp.min())
                                       if not frame.empty else None),
                "last_observed_ist": (str(frame.timestamp.max())
                                      if not frame.empty else None),
                "rolling_fixed_expiry_verified": False,
            }
            if not frame.empty:
                archive.writestr(csv_name, frame.to_csv(index=False))
            # Last member per request acts as a completed checkpoint.
            archive.writestr(metadata_name, json.dumps(record, sort_keys=True))
            complete[metadata_name] = record
            performed += 1
            if progress and performed % 25 == 0:
                print(f"{q.key}: {len(complete)}/{len(planned)} request members",
                      file=sys.stderr)
            del frame
        if len(complete) != len(planned):
            raise RuntimeError("Quarter completion mismatch; refusing upload")
        records = list(complete.values())
        rows = sum(x["rows"] for x in records)
        empty = sum(x["status"] == "EMPTY" for x in records)
        manifest = {
            "format_version": 1,
            "quarter": q.key,
            "quarter_start": q.quarter_start.isoformat(),
            "quarter_end_exclusive": q.quarter_end_exclusive.isoformat(),
            "eligible_from_inclusive": q.eligible_from.isoformat(),
            "eligible_to_exclusive": q.eligible_end_exclusive.isoformat(),
            "range_status": q.status,
            "source": "DhanHQ v2 /charts/rollingoption",
            "underlying": "NIFTY", "underlying_security_id": 13,
            "intervals_minutes": list(intervals),
            "series_selection": "WEEK/MONTH near/next/far; ATM offsets; CALL/PUT",
            "requests_planned": len(planned),
            "requests_with_successful_responses": len(records),
            "empty_responses": empty,
            "rows_exported": rows,
            "coverage_status": ("PARTIAL_EMPTY_RESPONSES" if empty else
                                "ALL_REQUESTS_RETURNED_NOT_CONTRACT_VERIFIED"),
            "historical_greeks_gamma_available": False,
            "fixed_option_contract_expiry_verified": False,
            "no_causal_vedic_or_numerology_claim": True,
            "warning": ("A rolling ATM-relative strike is not a verified "
                        "single expiry/security ID; no executable P&L inferred."),
        }
        if "manifest.json" in archive.namelist():
            raise RuntimeError("Unexpected existing final manifest")
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    # Atomic move to completed local file, then verify remote checksum.
    part_path.replace(final_path)
    outcome = upload_fn(final_path, remote)
    final_path.unlink()  # only our own validated staging ZIP, post MD5 verification
    return {
        "quarter": q.key, "status": outcome,
        "requests": len(planned), "new_calls": performed,
        "empty_responses": empty, "rows": rows,
        "coverage_status": manifest["coverage_status"],
    }


def run(args, *, client=None, sleeper=time.sleep,
        uploader=upload_verified, verify_remote=True):
    today = getattr(args, "as_of", None) or datetime.now(IST).date()
    quarters = plan_quarters(args.from_date, args.through or today, today=today)
    selected = [q for q in quarters if q.eligible_from is not None
                and (not args.quarter or q.key == args.quarter)]
    if args.quarter and not selected:
        raise ValueError("Selected quarter unavailable or outside date range")
    if args.quarter_limit is not None:
        if args.quarter_limit <= 0:
            raise ValueError("quarter-limit must be positive")
        selected = selected[:args.quarter_limit]
    planned = sum(sum(1 for _ in _each_query(q, args.intervals))
                  for q in selected)
    output = {
        "status": "PREVIEW_NO_API_CALLS",
        "requested_from": args.from_date.isoformat(),
        "requested_through": (args.through or today).isoformat(),
        "vendor_retention_floor_as_of_date": five_year_start(today).isoformat(),
        "data_type": "NIFTY_EXPIRED_ROLLING_OPTIONS_NOT_FIXED_CONTRACT",
        "intervals_minutes": list(args.intervals),
        "quarters_total": len(quarters), "quarters_selected": len(selected),
        "estimated_readonly_dhan_calls": planned,
        "excluded_old_quarters": [q.key for q in quarters
                                 if q.eligible_from is None],
        "quarter_plan": [dict(asdict(q), **{
            "quarter_start": q.quarter_start.isoformat(),
            "quarter_end_exclusive": q.quarter_end_exclusive.isoformat(),
            "eligible_from": q.eligible_from.isoformat() if q.eligible_from else None,
            "eligible_end_exclusive": (q.eligible_end_exclusive.isoformat()
                                      if q.eligible_end_exclusive else None),
        }) for q in quarters],
        "drive_folder_id": DRIVE_FOLDER_ID,
        "archived_quarters": [],
        "orders_sent": 0,
        "verified_gamma_events": None,
    }
    if not args.execute:
        return output
    if args.pause < .25 or not math.isfinite(args.pause):
        raise ValueError("pause must be >= 0.25 seconds")
    if verify_remote:
        _verify_remote(args.remote)
    if client is None:
        load_local_credentials()
        client = DhanClient()
    if str(client.profile().get("dataPlan", "")).lower() != "active":
        output["status"] = "BLOCKED_DHAN_DATA_PLAN_INACTIVE"
        return output
    staging = Path(args.staging).expanduser().resolve()
    output["status"] = "IN_PROGRESS"
    for q in selected:
        try:
            outcome = archive_quarter(
                q, client=client, staging=staging,
                remote=args.remote, intervals=args.intervals,
                pause=args.pause, sleeper=sleeper,
                upload_fn=uploader, progress=args.progress)
        except (DhanAPIError, ValueError, RuntimeError,
                OSError, zipfile.BadZipFile) as exc:
            output["status"] = "STOPPED_PARTIAL_LOCAL_STAGING_RETAINED"
            output["failure"] = {"quarter": q.key,
                                 "exception": type(exc).__name__,
                                 "dhan": exc.diagnostic() if isinstance(
                                     exc, DhanAPIError) else None}
            return output
        output["archived_quarters"].append(outcome)
    output["status"] = ("ALL_SELECTED_QUARTERS_UPLOADED"
                        if selected else "NO_ELIGIBLE_QUARTERS")
    output["all_requested_quarters_covered"] = bool(
        len(selected) == len(quarters)
        and all(x["status"] in ("UPLOADED_MD5_VERIFIED",
                                "ALREADY_PRESENT_MD5_VERIFIED")
                for x in output["archived_quarters"])
        and not output["excluded_old_quarters"])
    return output


def main():
    today = datetime.now(IST).date()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", type=date.fromisoformat,
                        default=date(2021, 1, 1))
    parser.add_argument("--through", type=date.fromisoformat, default=today)
    parser.add_argument("--quarter", type=str, default=None,
                        help="Optional e.g. 2021Q4")
    parser.add_argument("--quarter-limit", type=int, default=None,
                        help="Start with one quarter before bulk historical backfill")
    parser.add_argument("--remote", default="dhanarchive:",
                        help="rclone Drive remote ROOTED to this archive folder ID")
    parser.add_argument("--staging", default=str(
        Path.cwd().parent / "myTrade_Dhan_archive_staging"))
    parser.add_argument("--pause", type=float, default=.35)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--execute", action="store_true",
                        help="Explicit authorization for Dhan reads and verified Drive uploads")
    args = parser.parse_args()
    args.intervals = INTERVALS
    try:
        result = run(args)
    except (ValueError, RuntimeError, DhanAPIError) as exc:
        raise SystemExit("Stopped safely: " + type(exc).__name__) from None
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
