"""Durable, contract-scoped candle archive, independent of the current broker master."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

INTERVAL_MINUTES = {"ONE_MINUTE": 1, "THREE_MINUTE": 3, "FIVE_MINUTE": 5,
                    "TEN_MINUTE": 10, "FIFTEEN_MINUTE": 15, "THIRTY_MINUTE": 30,
                    "ONE_HOUR": 60, "ONE_DAY": 1440}


def contract_metadata(instrument: dict) -> dict:
    """Keep raw strike units; never guess a broker's strike scaling."""
    fields = ("exch_seg", "token", "symbol", "name", "expiry", "strike", "lotsize", "instrumenttype")
    result = {key: str(instrument.get(key) or "").strip() for key in fields}
    for key in ("exch_seg", "symbol", "name", "instrumenttype"):
        result[key] = result[key].upper()
    if not all(result[k] for k in ("exch_seg", "symbol", "token")):
        raise ValueError("Contract exchange, symbol and token are required.")
    if result["expiry"]:
        result["expiry"] = pd.Timestamp(result["expiry"]).date().isoformat()
    if "OPT" in result["instrumenttype"] or "FUT" in result["instrumenttype"]:
        if not result["expiry"]:
            raise ValueError("Derivative contracts require an expiry; token alone is unsafe.")
    if "OPT" in result["instrumenttype"] and not result["strike"]:
        raise ValueError("Option contracts require the original strike value.")
    lot = float(result["lotsize"] or 0)
    if not math.isfinite(lot) or lot < 1 or not lot.is_integer():
        raise ValueError("A positive historical lot size is required.")
    result["lotsize"] = str(int(lot))
    result["option_type"] = result["symbol"][-2:] if result["symbol"].endswith(("CE", "PE")) else ""
    return result


def contract_id(instrument: dict) -> str:
    meta = contract_metadata(instrument)
    # Tokens can be reused. Never use a token as the archive's primary identity.
    identity = [meta[k] for k in ("exch_seg", "symbol", "expiry", "strike", "instrumenttype")]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def local_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError("Invalid candle timestamp")
    return ts.tz_convert("Asia/Kolkata").tz_localize(None) if ts.tzinfo else ts


class HistoryArchive:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS contracts (
                    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, captured_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS candles (
                    contract_id TEXT NOT NULL, interval TEXT NOT NULL, timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL, source TEXT NOT NULL, captured_at TEXT NOT NULL,
                    PRIMARY KEY(contract_id, interval, timestamp));
                CREATE TABLE IF NOT EXISTS revisions (
                    id INTEGER PRIMARY KEY, contract_id TEXT NOT NULL, interval TEXT NOT NULL,
                    timestamp TEXT NOT NULL, payload TEXT NOT NULL, source TEXT NOT NULL,
                    captured_at TEXT NOT NULL);
            ''')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def save(self, instrument: dict, interval: str, frame: pd.DataFrame, *, source: str,
             now=None) -> str:
        """Atomically merge completed bars; retain every replaced observation as a revision."""
        if interval not in INTERVAL_MINUTES:
            raise ValueError("Unsupported archive interval")
        if not source.strip():
            raise ValueError("Data source is required")
        meta = contract_metadata(instrument)
        identity = contract_id(meta)
        required = {"timestamp", "open", "high", "low", "close"}
        if not required.issubset(frame.columns):
            raise ValueError("Candles require timestamp, open, high, low and close")
        current = local_timestamp(now if now is not None else pd.Timestamp.now(tz="Asia/Kolkata"))
        captured = datetime.now(timezone.utc).isoformat()
        rows = []
        for raw in frame.to_dict("records"):
            ts = local_timestamp(raw["timestamp"])
            if ts + pd.Timedelta(minutes=INTERVAL_MINUTES[interval]) > current:
                continue  # Still forming bars never become historical training evidence.
            prices = [float(raw[k]) for k in ("open", "high", "low", "close")]
            o, h, l, c = prices
            if not all(math.isfinite(p) and p >= 0 for p in prices) or l > min(o, c) or h < max(o, c) or l > h:
                raise ValueError("Invalid OHLC candle")
            payload = {}
            for key, value in raw.items():
                if key == "timestamp":
                    continue
                if pd.isna(value):
                    payload[key] = None
                elif hasattr(value, "item"):
                    payload[key] = value.item()
                else:
                    payload[key] = value
            payload.update(dict(zip(("open", "high", "low", "close"), prices)))
            # Record historical contract sizing, even after the live catalogue changes.
            payload["lot_size"] = int(meta["lotsize"])
            rows.append((ts.isoformat(), json.dumps(payload, sort_keys=True, allow_nan=False)))
        with closing(self._connect()) as db, db:
            existing = db.execute("SELECT metadata FROM contracts WHERE id=?", (identity,)).fetchone()
            if existing and json.loads(existing[0])["lotsize"] != meta["lotsize"]:
                raise ValueError("Historical lot size conflict; preserve the original contract metadata.")
            db.execute("INSERT OR IGNORE INTO contracts VALUES (?, ?, ?)",
                       (identity, json.dumps(meta, sort_keys=True), captured))
            for ts, payload in rows:
                key = (identity, interval, ts)
                old = db.execute("SELECT payload, source, captured_at FROM candles WHERE contract_id=? AND interval=? AND timestamp=?", key).fetchone()
                if old and old[0] == payload and old[1] == source:
                    continue
                # Tick-only reconstruction is lower quality than a broker/import candle.
                if old and source == "angel-live-ticks" and old[1] != source:
                    continue
                if old:
                    db.execute("INSERT INTO revisions(contract_id,interval,timestamp,payload,source,captured_at) VALUES (?,?,?,?,?,?)", (*key, *old))
                db.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)", (*key, payload, source, captured))
        return identity

    def catalogue(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute('''SELECT c.id,c.metadata,b.interval,COUNT(*),MIN(b.timestamp),MAX(b.timestamp)
                FROM contracts c JOIN candles b ON c.id=b.contract_id
                GROUP BY c.id,b.interval ORDER BY MAX(b.timestamp) DESC''').fetchall()
        return [dict(id=r[0], **json.loads(r[1]), interval=r[2], candles=r[3], first=r[4], last=r[5]) for r in rows]

    def load(self, identity: str, interval: str) -> pd.DataFrame:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT timestamp,payload,source,captured_at FROM candles WHERE contract_id=? AND interval=? ORDER BY timestamp", (identity, interval)).fetchall()
        frame = pd.DataFrame([dict(json.loads(p), timestamp=t, archive_source=s, archived_at=a) for t,p,s,a in rows])
        if not frame.empty:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        return frame

    def backup(self, destination: Path) -> Path:
        """SQLite online backup creates a consistent snapshot even during collection."""
        destination = Path(destination)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise ValueError("Choose a new backup filename, different from the live archive.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
        return destination
