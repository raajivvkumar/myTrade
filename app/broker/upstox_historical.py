"""Read-only Upstox v2 expired NIFTY options; no requests at import time."""
from __future__ import annotations
import math
import os
from datetime import date
from urllib.parse import quote
import pandas as pd
import requests
from dotenv import load_dotenv

BASE = "https://api.upstox.com/v2"
NIFTY = "NSE_INDEX|Nifty 50"
INTERVALS = {"1minute", "3minute", "5minute", "15minute", "30minute", "day"}


def _data(response):
    if not isinstance(response, dict) or response.get("status") != "success":
        raise RuntimeError("Upstox returned an unsuccessful data response")
    if "data" not in response:
        raise ValueError("Missing data in Upstox response")
    return response["data"]


def validate_contract(raw, expiry):
    required = ("instrument_key", "trading_symbol", "strike_price",
                "instrument_type", "lot_size", "expiry", "underlying_key")
    if not isinstance(raw, dict) or any(raw.get(k) in (None, "") for k in required):
        raise ValueError("Missing mandatory contract identity")
    if str(raw["expiry"]) != expiry.isoformat():
        raise ValueError("Contract expiry mismatch")
    if raw["underlying_key"] != NIFTY or raw["instrument_type"] not in ("CE", "PE"):
        raise ValueError("Only NIFTY CE/PE contracts are supported")
    key = str(raw["instrument_key"])
    if not key.startswith("NSE_FO|") or any(x in key for x in ("/", "?", "#")):
        raise ValueError("Unexpected expired instrument key")
    symbol = str(raw["trading_symbol"])
    if not symbol.startswith("NIFTY ") or (" " + raw["instrument_type"] + " ") not in symbol:
        raise ValueError("Trading symbol and type mismatch")
    strike, lot = float(raw["strike_price"]), float(raw["lot_size"])
    if not math.isfinite(strike) or strike <= 0:
        raise ValueError("Invalid strike")
    if not math.isfinite(lot) or lot < 1 or not lot.is_integer():
        raise ValueError("Invalid lot size")
    return dict(instrument_key=key, symbol=symbol, strike_price=strike,
                option_type=raw["instrument_type"], lot_size=int(lot),
                expiry=expiry.isoformat(), underlying_key=NIFTY)


def find_contract(contracts, expiry, strike, side):
    if side not in ("CE", "PE") or not math.isfinite(strike) or strike <= 0:
        raise ValueError("Invalid strike/side")
    found = [v for v in contracts if v["expiry"] == expiry.isoformat()
             and v["strike_price"] == strike and v["option_type"] == side]
    if len(found) != 1:
        raise ValueError(f"Expected exactly one NIFTY {expiry} {strike:g} {side}, found {len(found)}")
    return found[0]


def normalize_candles(response, contract, start, end):
    data = _data(response)
    if not isinstance(data, dict) or not isinstance(data.get("candles"), list):
        raise ValueError("Upstox response requires data.candles list")
    rows = data["candles"]
    cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    if any(not isinstance(row, list) or len(row) != 7 for row in rows):
        raise ValueError("Invalid seven-column candle shape")
    frame = pd.DataFrame(rows, columns=cols)
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True).dt.tz_convert(
        "Asia/Kolkata").dt.tz_localize(None)
    for col in cols[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if frame.isna().any().any():
        raise ValueError("Missing or invalid numeric candle data")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Nonpositive option OHLC")
    if (frame[["volume", "oi"]] < 0).any().any():
        raise ValueError("Negative volume/OI")
    if ((frame.low > frame[["open", "close"]].min(axis=1)) |
        (frame.high < frame[["open", "close"]].max(axis=1)) |
        (frame.low > frame.high)).any():
        raise ValueError("Inconsistent OHLC")
    if frame.timestamp.duplicated().any():
        raise ValueError("Duplicate contract-minute timestamps")
    if not frame.timestamp.dt.date.between(start, end).all():
        raise ValueError("Candles outside requested dates")
    if (frame.timestamp.dt.date > date.fromisoformat(contract["expiry"])).any():
        raise ValueError("Candles after contract expiry")
    for field in ("instrument_key", "symbol", "strike_price", "option_type", "expiry", "lot_size"):
        frame[field] = contract[field]
    frame["source_quality"] = "UPSTOX_UNVALIDATED_NEEDS_NSE_CHECK"
    return frame.sort_values("timestamp").reset_index(drop=True)


class UpstoxClient:
    """Allowlisted GET endpoints only. No trading/order methods."""

    def __init__(self, token=None, session=None):
        load_dotenv()
        self.token = (token if token is not None else os.getenv("UPSTOX_ACCESS_TOKEN", "")).strip()
        if not self.token:
            raise RuntimeError("Set UPSTOX_ACCESS_TOKEN in local .env file")
        self.session = session if session is not None else requests.Session()

    def _get(self, path, params=None):
        allowed = (path in ("/user/profile", "/expired-instruments/expiries",
                           "/expired-instruments/option/contract")
                   or path.startswith("/expired-instruments/historical-candle/"))
        if not allowed or any(x in path for x in ("..", "?", "#")):
            raise ValueError("Only allowlisted read-only Upstox endpoints")
        try:
            reply = self.session.get(
                BASE + path, headers={"accept": "application/json",
                    "Authorization": "Bearer " + self.token},
                params=params, timeout=25)
        except requests.RequestException as exc:
            raise RuntimeError("Upstox connection/timeout error") from exc
        if reply.status_code != 200:
            raise RuntimeError(f"Upstox HTTP {reply.status_code}; check fresh token or Plus plan")
        try:
            return _data(reply.json())
        except ValueError as exc:
            raise RuntimeError("Invalid Upstox JSON") from exc

    def profile_active(self):
        return self._get("/user/profile").get("is_active") is True

    def expiries(self):
        items = self._get("/expired-instruments/expiries", {"instrument_key": NIFTY})
        if not isinstance(items, list):
            raise ValueError("Expected expiry list")
        return sorted({date.fromisoformat(item) for item in items})

    def contracts(self, expiry):
        items = self._get("/expired-instruments/option/contract",
                          {"instrument_key": NIFTY, "expiry_date": expiry.isoformat()})
        if not isinstance(items, list):
            raise ValueError("Expected expired contracts list")
        return [validate_contract(c, expiry) for c in items]

    def candles(self, contract, start, end, interval="1minute"):
        if interval not in INTERVALS or end < start or end > date.fromisoformat(contract["expiry"]):
            raise ValueError("Invalid interval or date range")
        key = contract["instrument_key"]
        if not key.startswith("NSE_FO|") or "/" in key:
            raise ValueError("Invalid expired contract key")
        path = ("/expired-instruments/historical-candle/"
                + quote(key, safe="") + "/" + interval
                + "/" + end.isoformat() + "/" + start.isoformat())
        result = self._get(path)
        return normalize_candles({"status": "success", "data": result}, contract, start, end)
