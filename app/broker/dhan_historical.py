"""DhanHQ v2 read-only expired rolling option data for NIFTY.

WARNING: ATM relative position is not a fixed option contract.
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests

URL = "https://api.dhan.co/v2"
FIELDS = ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"]
STRIKE_RE = re.compile(r"ATM(?:[+-](?:[1-9]|10))?$")


class DhanAPIError(RuntimeError):
    """Only controlled diagnostics; never store headers or broker free text."""

    def __init__(self, category, *, http_status=None, provider_code=None):
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        messages = {
            "NETWORK_ERROR": "DhanHQ network/timeout error",
            "HTTP_ERROR": f"DhanHQ HTTP {self.http_status}; verify access token, plan and limits",
            "INVALID_JSON": "Invalid JSON from DhanHQ",
            "API_ERROR": "DhanHQ unsuccessful response; check plan or request parameters",
        }
        self.category = category if category in messages else "API_ERROR"
        self.provider_code = safe_provider_code(provider_code)
        super().__init__(messages[self.category] +
                         (f"; provider code {self.provider_code}" if self.provider_code else ""))

    def diagnostic(self):
        return dict(reason=self.category, http_status=self.http_status,
                    provider_code=self.provider_code)


def safe_provider_code(value):
    # Official Dhan error codes are numeric or DH-NNN, never arbitrary messages.
    text = str(value) if type(value) in (str, int) else ""
    return text if re.fullmatch(r"(?:DH-)?[0-9]{3}", text) else None


def response_error_code(data):
    return safe_provider_code(data.get("errorCode")) if isinstance(data, dict) else None


@dataclass(frozen=True)
class RollingQuery:
    start: date
    end: date  # exclusive
    expiry_flag: str = "MONTH"
    expiry_code: int = 0
    strike: str = "ATM"
    side: str = "PUT"
    interval: int = 1

    def __post_init__(self):
        if self.start >= self.end or (self.end - self.start).days > 30:
            raise ValueError("Use a positive window of at most 30 calendar days")
        if self.expiry_flag not in ("MONTH", "WEEK") or self.expiry_code not in (0, 1, 2):
            raise ValueError("Unsupported expiry flag/code")
        if not STRIKE_RE.fullmatch(self.strike):
            raise ValueError("Only ATM, ATM+1..ATM+10, ATM-1..ATM-10 supported")
        if self.side not in ("CALL", "PUT") or self.interval not in (1, 5, 15, 25, 60):
            raise ValueError("Unsupported option side or interval")

    def payload(self):
        return dict(exchangeSegment="NSE_FNO", interval=str(self.interval), securityId=13,
                    instrument="OPTIDX", expiryFlag=self.expiry_flag, expiryCode=self.expiry_code,
                    strike=self.strike, drvOptionType=self.side, requiredData=FIELDS.copy(),
                    fromDate=self.start.isoformat(), toDate=self.end.isoformat())


def windows(start: date, end: date):
    if end <= start:
        raise ValueError("End must be after start")
    while start < end:
        following = min(end, start + timedelta(days=30))
        yield start, following
        start = following


def normalize(response: dict, query: RollingQuery) -> pd.DataFrame:
    if not isinstance(response.get("data"), dict):
        raise ValueError("No data object in DhanHQ response")
    source = response["data"].get("ce" if query.side == "CALL" else "pe")
    if source is None:
        return pd.DataFrame()
    if not isinstance(source, dict):
        raise ValueError("Option data must be a JSON object")
    keys = ("timestamp", "open", "high", "low", "close", "strike")
    if any(not isinstance(source.get(k), list) for k in keys):
        raise ValueError("Missing timestamp/OHLC/strike arrays")
    n = len(source["timestamp"])
    if any(source.get(k) is not None and (not isinstance(source[k], list) or len(source[k]) != n)
           for k in (*keys, "volume", "oi", "iv", "spot")):
        raise ValueError("DhanHQ parallel arrays have mismatched lengths")
    if not n:
        return pd.DataFrame()
    ts = pd.to_datetime(source["timestamp"], unit="s", utc=True, errors="coerce")
    frame = pd.DataFrame({"timestamp": ts.tz_convert("Asia/Kolkata").tz_localize(None)})
    for name, key in (("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"),
                      ("volume", "volume"), ("oi", "oi"), ("iv", "iv"),
                      ("actual_strike", "strike"), ("spot", "spot")):
        frame[name] = pd.to_numeric(source.get(key, [None] * n), errors="coerce")
    required = ["timestamp", "open", "high", "low", "close", "actual_strike"]
    if frame[required].isna().any().any():
        raise ValueError("Invalid or missing candle, timestamp, or actual strike")
    if (frame[["open", "high", "low", "close", "actual_strike"]] <= 0).any().any():
        raise ValueError("Non-positive OHLC or actual strike")
    if ((frame.low > frame[["open", "close"]].min(axis=1))
        | (frame.high < frame[["open", "close"]].max(axis=1))
        | (frame.low > frame.high)).any():
        raise ValueError("Invalid OHLC range")
    if (frame["volume"].dropna() < 0).any() or (frame["oi"].dropna() < 0).any():
        raise ValueError("Negative volume or open interest")
    if frame.duplicated(["timestamp", "actual_strike"]).any():
        raise ValueError("Duplicate timestamp/actual-strike key")
    frame["relative_strike"] = query.strike
    frame["option_type"] = query.side
    frame["expiry_flag"] = query.expiry_flag
    frame["expiry_code"] = query.expiry_code
    frame["source_quality"] = "UNVALIDATED_ROLLING_NOT_FIXED_CONTRACT"
    return frame.sort_values(["timestamp", "actual_strike"]).reset_index(drop=True)


class DhanClient:
    """Fixed read-only endpoints, token supplied only at request time."""

    def __init__(self, token=None, session=None):
        self.token = (token if token is not None else os.getenv("DHAN_ACCESS_TOKEN", "")).strip()
        if not self.token:
            raise RuntimeError("Set DHAN_ACCESS_TOKEN in your local environment")
        self.session = session if session is not None else requests.Session()

    def _call(self, method, path, payload=None):
        if (method, path) not in (("GET", "/profile"), ("POST", "/charts/rollingoption"),
                                  ("POST", "/charts/intraday")):
            raise ValueError("Only allowlisted read-only Data API operations are permitted")
        headers = {"accept": "application/json", "access-token": self.token}
        if payload is not None:
            headers["content-type"] = "application/json"
        try:
            resp = self.session.request(method, URL + path, headers=headers, json=payload,
                                        timeout=30, allow_redirects=False)
        except requests.RequestException:
            raise DhanAPIError("NETWORK_ERROR") from None
        if resp.status_code != 200:
            try:
                error_data = resp.json()
            except (ValueError, AttributeError):
                error_data = None
            raise DhanAPIError("HTTP_ERROR", http_status=resp.status_code,
                               provider_code=response_error_code(error_data)) from None
        try:
            data = resp.json()
        except ValueError:
            raise DhanAPIError("INVALID_JSON", http_status=resp.status_code) from None
        if (not isinstance(data, dict)
                or str(data.get("status", "")).lower() in ("failed", "failure", "error")
                or data.get("errorCode") not in (None, "", 0, "0")):
            raise DhanAPIError("API_ERROR", http_status=resp.status_code,
                               provider_code=response_error_code(data)) from None
        return data

    def profile(self):
        response = self._call("GET", "/profile")
        return {key: response.get(key) for key in ("dataPlan", "dataValidity", "tokenValidity")}

    def rolling(self, query: RollingQuery):
        return normalize(self._call("POST", "/charts/rollingoption", query.payload()), query)
