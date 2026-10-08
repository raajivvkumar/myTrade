"""Read-only Upstox Basic/Analytics Token 1-minute candle availability check.

Supports index/India VIX and only CURRENT option contracts resolved from
a live option chain. Never calls expired-instrument or order APIs.
Nothing is persisted; historical option Greeks are NOT fabricated.
"""
from __future__ import annotations

import math
import os
import re
from datetime import date, datetime, time
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from app.broker.upstox_chain import get_option_chain
from app.research.gamma_lab import normalize_chain

IST = ZoneInfo("Asia/Kolkata")
BASE = "https://api.upstox.com/v3"
INDEX_KEYS = {"index": "NSE_INDEX|Nifty 50", "vix": "NSE_INDEX|India VIX"}
OPTION_KEY_RE = re.compile(r"^NSE_FO\|[A-Za-z0-9_-]+$")
SESSION_START = time(9, 15)
SESSION_LAST_START = time(15, 29)


def _access_token(token: str | None) -> str:
    load_dotenv()
    value = token if token is not None else os.getenv("UPSTOX_ANALYTICS_TOKEN", "")
    if not value or not value.strip():
        raise RuntimeError("Set UPSTOX_ANALYTICS_TOKEN in your private local .env")
    return value.strip()


def resolve_active_option(expiry: str, strike: float, side: str, *,
                          token: str | None = None, session=None) -> dict:
    """One exact NIFTY contract, never synthetic/rolling ATM identity."""
    if side not in ("CE", "PE") or not math.isfinite(strike) or strike <= 0:
        raise ValueError("Specify a positive strike and CE or PE")
    frame = normalize_chain(
        get_option_chain(expiry=expiry, token=token, session=session)
    )
    if frame.empty:
        raise ValueError("Current chain empty; no option contract selected")
    selected = frame.loc[
        frame.side.eq(side) & (frame.strike_price - strike).abs().le(0.001)
    ]
    if len(selected) != 1:
        raise ValueError(
            "Strike/side was not uniquely listed in the ACTIVE chain; "
            "no expired-option fallback"
        )
    item = selected.iloc[0]
    actual_expiry = date.fromisoformat(str(item.expiry))
    if actual_expiry < datetime.now(IST).date():
        raise ValueError("Expired contract cannot be queried using Basic API")
    return {
        "instrument_key": str(item.instrument_key),
        "expiry": actual_expiry.isoformat(),
        "strike_price": float(item.strike_price),
        "option_type": side,
    }


def fetch_day_1m(instrument_key: str, trading_day: str, *,
                 token: str | None = None, session=None) -> list[dict]:
    """GET one date of V3 candles, validate identity/shape and return in memory."""
    if instrument_key not in INDEX_KEYS.values() and not OPTION_KEY_RE.fullmatch(instrument_key):
        raise ValueError("Only NIFTY 50, India VIX or NSE_FO exact keys are accepted")
    try:
        day = date.fromisoformat(trading_day)
        if trading_day != day.isoformat():
            raise ValueError()
    except ValueError as exc:
        raise ValueError("Date must be YYYY-MM-DD") from exc
    if day > datetime.now(IST).date():
        raise ValueError("Future trading dates cannot have observed candles")

    url = (
        BASE + "/historical-candle/" + quote(instrument_key, safe="") +
        "/minutes/1/" + trading_day + "/" + trading_day
    )
    client = session if session is not None else requests.Session()
    try:
        response = client.get(
            url,
            headers={"Accept": "application/json",
                     "Authorization": "Bearer " + _access_token(token)},
            timeout=25,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Upstox historical candle request failed") from exc
    if response.status_code != 200:
        raise RuntimeError(
            f"Upstox historical HTTP {response.status_code}; "
            "check Analytics Token / Basic entitlement / exact active key"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Invalid Upstox historical JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise RuntimeError("Upstox historical response unsuccessful")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("candles"), list):
        raise ValueError("Historical response missing candles list")
    result = []
    observed = set()
    for candle in data["candles"]:
        if not isinstance(candle, list) or len(candle) < 7:
            raise ValueError("Historical candle must have 7 fields")
        try:
            dt = datetime.fromisoformat(str(candle[0]))
            if dt.tzinfo is None:
                raise ValueError("Candle missing timezone")
            dt = dt.astimezone(IST)
            nums = [float(n) for n in candle[1:7]]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Malformed historical minute candle") from exc
        opening, high, low, close, volume, oi = nums
        if (dt.date() != day or dt.second or dt.microsecond or
                dt.isoformat() in observed or
                not all(math.isfinite(x) for x in nums) or
                min(opening, high, low, close) <= 0 or
                volume < 0 or oi < 0 or
                low > min(opening, close) or high < max(opening, close)):
            raise ValueError("Invalid/duplicate/out-of-date historical candle")
        observed.add(dt.isoformat())
        result.append({
            "timestamp": dt.isoformat(), "open": opening, "high": high,
            "low": low, "close": close, "volume": volume, "oi": oi,
            "instrument_key": instrument_key,
        })
    return sorted(result, key=lambda item: item["timestamp"])


def candle_quality(candles: list[dict]) -> dict:
    """Surface gaps; do not turn missing timestamps into invented prices."""
    minute_ids = {
        datetime.fromisoformat(row["timestamp"]).astimezone(IST).strftime("%H:%M")
        for row in candles
        if SESSION_START <= datetime.fromisoformat(row["timestamp"]).astimezone(IST).time() <= SESSION_LAST_START
    }
    return {
        "observed_candles": len(candles),
        "regular_session_minutes": len(minute_ids),
        "missing_regular_minutes": 375 - len(minute_ids),
        "research_ready_contiguous_session": len(minute_ids) == 375,
        "first": candles[0]["timestamp"] if candles else None,
        "last": candles[-1]["timestamp"] if candles else None,
    }
