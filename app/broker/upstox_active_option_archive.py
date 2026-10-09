"""Upstox Basic / free Analytics Token: ACTIVE fixed NIFTY option history.

Dry-run and explicit GET-only pilot. Not for expired/Plus endpoints, orders,
live execution or broker-independent historic Greeks. Data are UNVERIFIED.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from app.data.market_archive import immutable_copy, sha256_file

API = "https://api.upstox.com"
INDEX = "NSE_INDEX|Nifty 50"
IST = ZoneInfo("Asia/Kolkata")
ONE_MINUTE = pd.Timedelta(minutes=1)
SESSION_OPEN = time(9, 15)
SESSION_LAST = time(15, 29)
DAY_BARS = 375


def _iso_date(s: str) -> date:
    try:
        parsed = date.fromisoformat(s)
    except ValueError as exc:
        raise ValueError("Date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != s:
        raise ValueError("Date must be YYYY-MM-DD")
    return parsed


def _valid_option_key(raw) -> str:
    import re
    key = str(raw or "")
    if not re.fullmatch(r"NSE_FO\|[A-Za-z0-9_-]+", key):
        raise ValueError("Malformed NSE_FO option instrument key")
    return key


class ActiveOptionClient:
    """Read-only, allowlisted GET requests, never order/post APIs."""

    def __init__(self, *, token: str | None = None, session=None):
        load_dotenv()
        self.token = (
            token if token is not None else os.environ.get("UPSTOX_ANALYTICS_TOKEN", "")
        ).strip()
        if not self.token:
            raise RuntimeError("UPSTOX_ANALYTICS_TOKEN missing in PRIVATE local .env")
        self.session = session if session is not None else requests.Session()

    def _get(self, url: str, *, params: dict | None = None):
        if not (
            url == API + "/v2/option/chain"
            or url.startswith(API + "/v3/historical-candle/")
        ):
            raise ValueError("Only allowlisted GET market data endpoints permitted")
        try:
            response = self.session.get(
                url, params=params, timeout=30,
                headers={
                    "Accept": "application/json",
                    "Authorization": "Bearer " + self.token,
                },
            )
        except requests.RequestException as exc:
            raise RuntimeError("Upstox market-data network/timeout failure") from exc
        if response.status_code != 200:
            raise RuntimeError(
                f"Upstox read-only HTTP {response.status_code}: token, entitlement or instrument"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Invalid Upstox market-data JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise RuntimeError("Upstox reported unsuccessful market-data response")
        return payload.get("data")

    def current_contract(self, *, expiry: str, strike: float, side: str):
        if side not in ("CE", "PE") or not math.isfinite(strike) or strike <= 0:
            raise ValueError("Specify exact positive strike and CE or PE")
        if expiry not in {
            "current_week", "next_week", "far_week",
            "current_month", "next_month", "far_month",
        }:
            specified = _iso_date(expiry)
            if specified < datetime.now(IST).date():
                raise ValueError("No expired-contract fallback on Basic plan")
        rows = self._get(
            API + "/v2/option/chain",
            params={"instrument_key": INDEX, "expiry_date": expiry},
        )
        if not isinstance(rows, list):
            raise ValueError("Option chain must contain a list")
        results = []
        for row in rows:
            if not isinstance(row, dict) or row.get("underlying_key") != INDEX:
                continue
            if not math.isclose(float(row.get("strike_price", 0)), strike, abs_tol=0.001):
                continue
            actual_expiry = _iso_date(str(row["expiry"]))
            if actual_expiry < datetime.now(IST).date():
                continue
            child = row.get("call_options" if side == "CE" else "put_options")
            if not isinstance(child, dict):
                continue
            results.append({
                "instrument_key": _valid_option_key(child.get("instrument_key")),
                "underlying_key": INDEX,
                "underlying": "NIFTY",
                "strike_price": float(row["strike_price"]),
                "option_type": side,
                "expiry": actual_expiry.isoformat(),
            })
        if len(results) != 1:
            raise ValueError("Requested strike/side not unique in ACTIVE option chain")
        return results[0]

    def candles(self, contract: dict, trading_day: date) -> pd.DataFrame:
        expiry = _iso_date(str(contract["expiry"]))
        if not isinstance(trading_day, date) or trading_day > datetime.now(IST).date():
            raise ValueError("Requested day must not be future")
        if expiry < datetime.now(IST).date() or trading_day > expiry:
            raise ValueError("Basic API does not import expired option contracts")
        if contract.get("underlying_key") != INDEX or contract.get("option_type") not in ("CE", "PE"):
            raise ValueError("Not an exact NIFTY CE/PE contract")
        key = _valid_option_key(contract.get("instrument_key"))
        encoded = quote(key, safe="")
        day = trading_day.isoformat()
        response = self._get(
            API + f"/v3/historical-candle/{encoded}/minutes/1/{day}/{day}"
        )
        return validate_candles(response, contract=contract, trading_day=trading_day)


def validate_candles(payload, *, contract: dict, trading_day: date) -> pd.DataFrame:
    if not isinstance(payload, dict) or not isinstance(payload.get("candles"), list):
        raise ValueError("Historical 1-minute payload must contain candles")
    raw = payload["candles"]
    cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    if any(not isinstance(x, list) or len(x) != 7 for x in raw):
        raise ValueError("Expected 7 candle fields")
    df = pd.DataFrame(raw, columns=cols)
    if df.empty:
        return df
    if any(not isinstance(x, str) or not (x.endswith("Z") or "+" in x or x.endswith("z")) for x in df.timestamp):
        raise ValueError("Timezone-aware source timestamps required")
    df.timestamp = pd.to_datetime(
        df.timestamp, utc=True, errors="raise",
    ).dt.tz_convert(IST).dt.tz_localize(None)
    if df.timestamp.isna().any() or df.timestamp.duplicated().any():
        raise ValueError("Duplicate or missing minute timestamp")
    if not df.timestamp.dt.date.eq(trading_day).all():
        raise ValueError("Candle date differs from requested trading day")
    if not df.timestamp.dt.time.between(SESSION_OPEN, SESSION_LAST).all():
        raise ValueError("Out-of-session option candle timestamp")
    if (df.timestamp.dt.second != 0).any() or (df.timestamp.dt.microsecond != 0).any():
        raise ValueError("Expected exact 1-minute timestamps")
    for col in cols[1:]:
        df[col] = pd.to_numeric(df[col], errors="raise")
    values = df[cols[1:]].to_numpy(dtype=float)
    if not math.isfinite(values.min()) or not math.isfinite(values.max()):
        raise ValueError("Nonfinite OHLCV/OI")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Nonpositive option OHLC")
    if (df[["volume", "oi"]] < 0).any().any():
        raise ValueError("Invalid volume or OI")
    if ((df.low > df[["open", "close"]].min(axis=1)) |
        (df.high < df[["open", "close"]].max(axis=1)) |
        (df.high < df.low)).any():
        raise ValueError("Impossible OHLC")
    df = df.sort_values("timestamp").reset_index(drop=True)
    for field in ("instrument_key", "underlying_key", "underlying",
                  "strike_price", "option_type", "expiry"):
        df[field] = contract[field]
    df["data_quality"] = "UPSTOX_BASIC_ACTIVE_UNVERIFIED"
    return df


def quality(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"observed": 0, "missing_regular_minutes": DAY_BARS,
                "research_ready_full_session": False, "first_ist": None,
                "last_ist": None}
    minutes = len(frame)
    first, last = frame.timestamp.min(), frame.timestamp.max()
    full = (
        minutes == DAY_BARS
        and first.time() == SESSION_OPEN
        and last.time() == SESSION_LAST
        and frame.timestamp.diff().iloc[1:].eq(ONE_MINUTE).all()
    )
    return {"observed": minutes, "missing_regular_minutes": DAY_BARS - minutes,
            "research_ready_full_session": bool(full),
            "first_ist": str(first), "last_ist": str(last)}


def target_path(source_dir: Path, contract: dict, day: date) -> Path:
    key = _valid_option_key(contract["instrument_key"])
    expiry = _iso_date(contract["expiry"]).isoformat()
    strike = float(contract["strike_price"])
    if not math.isfinite(strike) or strike <= 0 or contract["option_type"] not in ("CE", "PE"):
        raise ValueError("Invalid option archive identity")
    identity = sha256(key.encode("utf-8")).hexdigest()[:16]
    return (Path(source_dir) / "NIFTY" / expiry
            / f"{strike:g}{contract['option_type']}_{identity}"
            / f"{day.isoformat()}.parquet")


def _write_immutable_bytes(target: Path, contents: bytes) -> None:
    if target.exists():
        if target.read_bytes() != contents:
            raise ValueError(f"Archive conflict: refusing overwrite of {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive mode ensures an existing historical capture cannot be clobbered.
    with target.open("xb") as output:
        output.write(contents)
        output.flush()
        os.fsync(output.fileno())


def archive_one_day(
    frame: pd.DataFrame, contract: dict, trading_day: date, *,
    source_dir: Path, mirror_dir: Path,
) -> dict:
    """Archive immutable exact-contract candles and SHA256-verify offline mirror."""
    if frame.empty:
        raise ValueError("Cannot archive empty option history")
    if trading_day >= datetime.now(IST).date() and datetime.now(IST).time() < time(15, 31):
        raise ValueError("Do not archive still-forming same-day minute history")
    primary = Path(source_dir).resolve()
    backup = Path(mirror_dir).resolve()
    if primary == backup or primary in backup.parents or backup in primary.parents:
        raise ValueError("Source and independent backup directories must be separate")
    path = target_path(primary, contract, trading_day)
    if not all(frame.instrument_key.eq(contract["instrument_key"])):
        raise ValueError("Candle instrument key mismatch")
    if not frame.timestamp.dt.date.eq(trading_day).all():
        raise ValueError("Candle trading-day mismatch")
    # Produce both immutable files from one validated dataframe. Missing minutes
    # are recorded as UNVERIFIED and will not be filled or counted as complete.
    status = quality(frame)
    io = BytesIO()
    frame.to_parquet(io, index=False)
    payload = io.getvalue()
    metadata = {
        "schema": "MYTRADE_NIFTY_ACTIVE_OPTION_1M_V1",
        "data_kind": "EXACT_CONTRACT_OPTION_CANDLES_UNVERIFIED",
        "api": "Upstox Historical Candle V3",
        "source": "Upstox Basic Free Analytics Token - read-only",
        "contract": contract, "day_ist": trading_day.isoformat(),
        "interval_minutes": 1, "quality": status,
        "parquet_sha256": sha256(payload).hexdigest(),
        "note": "No historical Greeks/IV or bid/ask; needs independent crosscheck.",
    }
    jsonfile = path.with_suffix(".json")
    # Validate any existing file pair BEFORE touching either disk.
    if path.exists() != jsonfile.exists():
        raise ValueError("Incomplete existing source pair; investigate before retry")
    metadata_bytes = (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode()
    _write_immutable_bytes(path, payload)
    _write_immutable_bytes(jsonfile, metadata_bytes)
    mirrored_data = backup / path.relative_to(primary)
    mirrored_meta = backup / jsonfile.relative_to(primary)
    if mirrored_data == path:
        raise ValueError("Backup path equals primary")
    data_hash = immutable_copy(path, mirrored_data)
    meta_hash = immutable_copy(jsonfile, mirrored_meta)
    if data_hash != metadata["parquet_sha256"] or meta_hash != sha256_file(jsonfile):
        raise IOError("Offline backup checksum verification failed")
    return {"path": str(path), "backup": str(mirrored_data),
            "rows": int(len(frame)), "sha256": data_hash,
            "quality": status, "independently_verified": False}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Read-only NIFTY ACTIVE option 1m pilot + opt-in immutable mirror"
    )
    p.add_argument("--expiry", required=True)
    p.add_argument("--strike", required=True, type=float)
    p.add_argument("--side", required=True, choices=("CE", "PE"))
    p.add_argument("--date", required=True, type=_iso_date)
    p.add_argument("--execute", action="store_true",
                   help="Allow 2 GET calls to live Upstox, otherwise dry-run")
    p.add_argument("--save", action="store_true",
                   help="Explicitly write immutable local Parquet + independent backup")
    p.add_argument("--source-dir", type=Path,
                   default=Path("data/raw/upstox/options_unvalidated"))
    p.add_argument("--backup-dir", type=Path,
                   default=Path("../MyTradeOfflineArchive/upstox_options_v3"))
    return p


def run(args, *, client=None) -> dict:
    if args.save and not args.execute:
        raise ValueError("--save requires --execute")
    if not math.isfinite(args.strike) or args.strike <= 0:
        raise ValueError("Positive exact strike required")
    if args.date > datetime.now(IST).date():
        raise ValueError("Future trading-day data is unavailable")
    if not args.execute:
        print("DRY RUN: no broker request, trading, data files or credentials needed")
        print(f"Would inspect NIFTY {args.expiry} {args.strike:g} {args.side} on {args.date}")
        return {"status": "DRY_RUN", "requests": 0}
    if args.save and args.date == datetime.now(IST).date() and datetime.now(IST).time() < time(15, 31):
        raise ValueError("Cannot save an incomplete live trading session")
    broker = client or ActiveOptionClient()
    contract = broker.current_contract(
        expiry=args.expiry, strike=args.strike, side=args.side
    )
    candles = broker.candles(contract, args.date)
    report = quality(candles)
    print(f"Upstox NIFTY {contract['expiry']} {args.strike:g} {args.side}, {args.date}")
    print(f"Minute candles: {report['observed']}; missing normal-session bars: "
          f"{report['missing_regular_minutes']}; full session: "
          f"{report['research_ready_full_session']}")
    if candles.empty:
        print("NO DATA: no archive created")
        return {"status": "NO_DATA", "requests": 2, "contract": contract, "quality": report}
    if not args.save:
        print("READ-ONLY PROBE: data remains in memory; use --save for local archive")
        return {"status": "IN_MEMORY", "requests": 2, "contract": contract, "quality": report}
    saved = archive_one_day(
        candles, contract, args.date,
        source_dir=args.source_dir, mirror_dir=args.backup_dir,
    )
    print("LOCAL ARCHIVE + OFFLINE MIRROR WRITTEN AND HASHED (unverified source)")
    print(f"Source: {saved['path']}")
    print(f"Offline mirror: {saved['backup']}")
    return {"status": "ARCHIVED_UNVERIFIED", "requests": 2,
            "contract": contract, "archive": saved}


def main():
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
