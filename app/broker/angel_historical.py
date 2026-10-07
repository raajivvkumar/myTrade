"""Historical candle retrieval through Angel One SmartAPI.

Authentication is intentionally kept separate from this module. Pass an
already-authenticated client when calling get_candles(). This module has no
SmartAPI import so offline history/backtests never trigger broker-side network
work during import.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import pandas as pd


SUPPORTED_INTERVALS: Final[set[str]] = {
    "ONE_MINUTE",
    "THREE_MINUTE",
    "FIVE_MINUTE",
    "TEN_MINUTE",
    "FIFTEEN_MINUTE",
    "THIRTY_MINUTE",
    "ONE_HOUR",
    "ONE_DAY",
}

CANDLE_COLUMNS: Final[list[str]] = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


def get_candles(
    client: Any,
    *,
    exchange: str,
    symbol_token: str,
    interval: str,
    from_datetime: datetime,
    to_datetime: datetime,
) -> pd.DataFrame:
    """Download a candle window and return a normalized DataFrame."""
    interval = interval.upper()
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(
            f"Unsupported interval {interval!r}. Choose one of: "
            + ", ".join(sorted(SUPPORTED_INTERVALS))
        )
    if from_datetime >= to_datetime:
        raise ValueError("from_datetime must be earlier than to_datetime")

    params = {
        "exchange": exchange.upper(),
        "symboltoken": str(symbol_token),
        "interval": interval,
        "fromdate": from_datetime.strftime("%Y-%m-%d %H:%M"),
        "todate": to_datetime.strftime("%Y-%m-%d %H:%M"),
    }

    response = client.getCandleData(params)
    if not response or not response.get("status"):
        message = (
            response.get("message", "Unknown historical-data error")
            if response
            else "Empty response"
        )
        error_code = response.get("errorcode", "") if response else ""
        raise RuntimeError(
            f"Angel historical request failed: {error_code} {message}".strip()
        )

    rows = response.get("data") or []
    frame = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
    if frame.empty:
        return frame

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    return (
        frame.dropna(subset=["timestamp"])
        .drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
