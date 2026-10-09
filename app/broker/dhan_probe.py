"""Small NIFTY data-access probe; no orders, archives, or automatic retries."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.broker.dhan_historical import DhanClient

IST = ZoneInfo("Asia/Kolkata")


def nifty_payload(day: date) -> dict:
    return {
        "securityId": "13", "exchangeSegment": "IDX_I", "instrument": "INDEX",
        "interval": "1", "oi": False,
        "fromDate": f"{day.isoformat()} 09:15:00",
        "toDate": f"{day.isoformat()} 09:30:00",
    }


def summarize_nifty(response: dict, day: date) -> dict:
    """Validate structure and OHLC, and report sample coverage without inventing bars."""
    fields = ("timestamp", "open", "high", "low", "close")
    if any(not isinstance(response.get(field), list) for field in fields):
        raise ValueError("Missing NIFTY timestamp/OHLC arrays")
    count = len(response["timestamp"])
    if any(len(response[field]) != count for field in fields):
        raise ValueError("NIFTY parallel arrays have mismatched lengths")
    if count == 0:
        raise ValueError("No NIFTY candles returned; check date, holiday and data availability")
    start = datetime.combine(day, time(9, 15), IST)
    end = datetime.combine(day, time(9, 30), IST)
    candles = []
    for values in zip(*(response[field] for field in fields)):
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in values):
            raise ValueError("Non-numeric or non-finite NIFTY timestamp/OHLC")
        epoch, opening, high, low, close = values
        try:
            timestamp = datetime.fromtimestamp(epoch, IST)
        except (OverflowError, OSError, ValueError):
            raise ValueError("Invalid NIFTY epoch timestamp") from None
        if not start <= timestamp < end or timestamp.second or timestamp.microsecond:
            raise ValueError("NIFTY timestamp outside requested minute window")
        if min(opening, high, low, close) <= 0 or not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError("Invalid NIFTY OHLC range")
        candles.append({"timestamp_ist": timestamp.isoformat(), "open": opening,
                        "high": high, "low": low, "close": close})
    stamps = [candle["timestamp_ist"] for candle in candles]
    if len(set(stamps)) != count or stamps != sorted(stamps):
        raise ValueError("Duplicate or out-of-order NIFTY timestamps")
    expected = [(start + timedelta(minutes=i)).isoformat() for i in range(15)]
    missing = [stamp for stamp in expected if stamp not in set(stamps)]
    return {"instrument": "NIFTY 50 INDEX", "candle_count": count,
            "expected_minutes": 15, "missing_minutes_ist": missing,
            "sample_coverage": "COMPLETE" if not missing else "INCOMPLETE",
            "first_timestamp_ist": stamps[0], "last_timestamp_ist": stamps[-1],
            "preview": candles[:3],
            "validation_scope": "STRUCTURE_OHLC_AND_WINDOW_ONLY",
            "independently_verified": False,
            "fixed_option_contract_verified": False}


def run_nifty_probe(day: date, *, execute: bool = False, client=None) -> dict:
    payload = nifty_payload(day)
    if not execute:
        return {"mode": "DRY_RUN", "network_requests": 0, "saved_files": 0,
                "request": payload}
    # Profile first; an inactive or unknown data plan never triggers the data call.
    client = client if client is not None else DhanClient()
    plan = client.profile()
    if str(plan.get("dataPlan", "")).strip().lower() != "active":
        raise RuntimeError("Dhan Data API plan not active; no historical call made")
    result = summarize_nifty(client._call("POST", "/charts/intraday", payload), day)
    return {"mode": "READ_ONLY_SAMPLE", "request": payload, "saved_files": 0,
            "data_plan": plan.get("dataPlan"), "data_validity": plan.get("dataValidity"),
            **result}
