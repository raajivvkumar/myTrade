"""GET-only Upstox Historical Candle V3 client for NIFTY index research.

Uses a user-local one-year read-only Analytics Token. Does not access Plus-only
expired option APIs, order endpoints, or network at import time.
"""
from __future__ import annotations

import math
import os
from datetime import date
from urllib.parse import quote

import pandas as pd
import requests
from dotenv import load_dotenv

API_URL = "https://api.upstox.com/v3"
INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50"}


def validate_index_candles(response: dict, *, start: date, end: date,
                           index: str = "NIFTY", minutes: int = 1) -> pd.DataFrame:
    """Reject invalid OHLC and preserve source-specific index identity.

    Volume and OI for cash indices may be zero/undefined; they are not options
    contract OI, which must never be inferred from this dataset.
    """
    if index not in INDEX_KEYS or minutes not in range(1, 16):
        raise ValueError("Unsupported index or interval")
    if start > end:
        raise ValueError("Start date is after end date")
    if not isinstance(response, dict) or response.get("status") != "success":
        raise ValueError("Invalid Upstox historical data response")
    values = response.get("data")
    if not isinstance(values, dict) or not isinstance(values.get("candles"), list):
        raise ValueError("Response missing data.candles")
    columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    rows = values["candles"]
    if any(not isinstance(row, list) or len(row) != 7 for row in rows):
        raise ValueError("Expected seven candle columns per observation")
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], utc=True, errors="coerce"
    ).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    for field in columns[1:]:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    if frame.isna().any().any():
        raise ValueError("Missing timestamp, price, volume or OI")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Non-positive index OHLC")
    if (frame[["volume", "oi"]] < 0).any().any():
        raise ValueError("Negative volume or OI")
    if ((frame.low > frame[["open", "close"]].min(axis=1)) |
        (frame.high < frame[["open", "close"]].max(axis=1)) |
        (frame.low > frame.high)).any():
        raise ValueError("Inconsistent index OHLC")
    if frame["timestamp"].duplicated().any():
        raise ValueError("Duplicate minute timestamps")
    if not frame["timestamp"].dt.date.between(start, end).all():
        raise ValueError("Candles outside the requested date window")
    frame["instrument_key"] = INDEX_KEYS[index]
    frame["index_symbol"] = index
    frame["interval_minutes"] = minutes
    frame["data_quality"] = "UNVALIDATED_UPSTOX_INDEX_V3"
    return frame.sort_values("timestamp").reset_index(drop=True)


class UpstoxIndexClient:
    """No trading permissions; allowlisted V3 market-history endpoint only."""

    def __init__(self, token: str | None = None, *, session=None):
        load_dotenv()
        self.token = (
            token if token is not None else os.getenv("UPSTOX_ANALYTICS_TOKEN", "")
        ).strip()
        if not self.token:
            raise RuntimeError("Set UPSTOX_ANALYTICS_TOKEN in local .env (do not share it)")
        self.session = session if session is not None else requests.Session()

    def history(self, *, index: str = "NIFTY", start: date,
                end: date, minutes: int = 1) -> pd.DataFrame:
        if index not in INDEX_KEYS:
            raise ValueError("Only NIFTY is currently supported")
        if minutes not in range(1, 16):
            raise ValueError("Select a minutes interval from 1 to 15")
        if start > end or (end - start).days > 27:
            raise ValueError("Maximum 28 inclusive calendar days per sample window")
        if start < date(2022, 1, 1):
            raise ValueError("Minute history before 2022 is not documented")
        instrument = quote(INDEX_KEYS[index], safe="")
        url = (f"{API_URL}/historical-candle/{instrument}/minutes/{minutes}/"
               f"{end.isoformat()}/{start.isoformat()}")
        try:
            response = self.session.get(
                url,
                headers={"accept": "application/json",
                         "Authorization": f"Bearer {self.token}"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Upstox V3 market-data network/timeout failure") from exc
        if response.status_code != 200:
            raise RuntimeError(
                f"Upstox V3 HTTP {response.status_code}; check token/access/dates"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Upstox V3 returned invalid JSON") from exc
        return validate_index_candles(
            payload, start=start, end=end, index=index, minutes=minutes
        )
