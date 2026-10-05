"""Causal next-candle forecasts and immutable live accuracy records.

Experimental rolling-drift model; OHLC forecasts are estimates, not probabilities.
Gamma exposure is option gamma * units. No Greeks are inferred from OHLC.
"""
from __future__ import annotations

import json
import sqlite3
from math import isfinite
from pathlib import Path

import pandas as pd

MODEL = "rolling_drift_v1"
PRICE_COLUMNS = ("open", "high", "low", "close")


def validate_candles(frame: pd.DataFrame) -> pd.DataFrame:
    missing = {"timestamp", *PRICE_COLUMNS}.difference(frame.columns)
    if missing:
        raise ValueError("Missing candle columns: " + ", ".join(sorted(missing)))
    data = frame.copy().reset_index(drop=True)
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("Invalid candle timestamp")
    if not data["timestamp"].is_monotonic_increasing or data["timestamp"].duplicated().any():
        raise ValueError("Candle timestamps must be unique and increasing")
    for column in PRICE_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
        if not data[column].map(lambda x: pd.notna(x) and isfinite(x) and x > 0).all():
            raise ValueError("OHLC prices must be finite and positive")
    if ((data["high"] < data[["open", "close", "low"]].max(axis=1)) |
            (data["low"] > data[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC range")
    return data


def gamma_exposure(option_gamma: float | None, lot_size: int, lots: int) -> float | None:
    if option_gamma is None:
        return None
    if not isfinite(option_gamma) or option_gamma < 0:
        raise ValueError("Option gamma must be finite and nonnegative")
    if lot_size < 1 or lots < 1 or int(lot_size) != lot_size or int(lots) != lots:
        raise ValueError("Lot size and lots must be positive integers")
    return float(option_gamma * lot_size * lots)


def forecast_next(frame: pd.DataFrame, *, window: int = 10) -> dict:
    """Use only supplied completed candles; never reads a future target."""
    if window < 2 or int(window) != window:
        raise ValueError("window must be an integer of at least 2")
    data = validate_candles(frame)
    if len(data) < window + 1:
        raise ValueError("Not enough completed candles for the forecast window")
    recent = data.tail(window)
    origin_close = float(data.iloc[-1]["close"])
    drift = float(data["close"].diff().tail(window).mean())
    predicted_close = max(0.000001, origin_close + drift)
    upper_wick = float((recent["high"] - recent[["open", "close"]].max(axis=1)).mean())
    lower_wick = float((recent[["open", "close"]].min(axis=1) - recent["low"]).mean())
    return {
        "model": MODEL, "window": window,
        "timestamp": pd.Timestamp(data.iloc[-1]["timestamp"]).isoformat(),
        "origin_close": origin_close,
        "predicted_open": origin_close,
        "predicted_high": max(origin_close, predicted_close) + upper_wick,
        "predicted_low": max(0.000001, min(origin_close, predicted_close) - lower_wick),
        "predicted_close": predicted_close,
        "baseline_close": origin_close,
    }


def score_forecast(prediction: dict, actual: dict, *, tolerance_pct: float = 0.1) -> dict:
    if not isfinite(tolerance_pct) or tolerance_pct < 0:
        raise ValueError("Tolerance must be finite and nonnegative")
    result = dict(prediction)
    for column in PRICE_COLUMNS:
        value = float(actual[column])
        if not isfinite(value) or value <= 0:
            raise ValueError("Actual prices must be finite and positive")
        result["actual_" + column] = value
        result[column + "_absolute_error"] = abs(float(prediction["predicted_" + column]) - value)
    error = result["close_absolute_error"]
    result["close_error_pct"] = error / result["actual_close"] * 100
    result["within_tolerance"] = result["close_error_pct"] <= tolerance_pct
    result["baseline_absolute_error"] = abs(prediction["baseline_close"] - result["actual_close"])
    expected = prediction["predicted_close"] - prediction["origin_close"]
    observed = result["actual_close"] - prediction["origin_close"]
    sign = lambda x: 0 if abs(x) < 1e-12 else (1 if x > 0 else -1)
    result["direction_correct"] = sign(expected) == sign(observed)
    result["actual_timestamp"] = pd.Timestamp(actual["timestamp"]).isoformat()
    result["status"] = "SCORED"
    return result


def accuracy_summary(records: pd.DataFrame) -> dict:
    scored = records[records["status"] == "SCORED"] if not records.empty else records
    if scored.empty:
        return {"scored": 0}
    return {
        "scored": len(scored),
        "close_mae": float(scored["close_absolute_error"].mean()),
        "close_rmse": float((scored["close_absolute_error"].pow(2).mean()) ** 0.5),
        "close_mape_pct": float(scored["close_error_pct"].mean()),
        "direction_accuracy_pct": float(scored["direction_correct"].mean() * 100),
        "within_tolerance_pct": float(scored["within_tolerance"].mean() * 100),
        "baseline_mae": float(scored["baseline_absolute_error"].mean()),
        "beats_baseline": bool(scored["close_absolute_error"].mean() <
                               scored["baseline_absolute_error"].mean()),
    }


def historical_accuracy(frame: pd.DataFrame, *, minutes: int = 5,
                        window: int = 10, tolerance_pct: float = 0.1) -> pd.DataFrame:
    data = validate_candles(frame)
    if minutes < 1 or int(minutes) != minutes:
        raise ValueError("minutes must be a positive integer")
    rows = []
    for index in range(window, len(data) - 1):
        # Missing bars and overnight session gaps are not next interval targets.
        if data.iloc[index + 1]["timestamp"] - data.iloc[index]["timestamp"] != pd.Timedelta(minutes=minutes):
            continue
        prediction = forecast_next(data.iloc[max(0, index - window):index + 1], window=window)
        if "option_gamma" in data:
            row = data.iloc[index]
            raw = row["option_gamma"]
            prediction["gamma_exposure"] = gamma_exposure(
                float(raw) if pd.notna(raw) else None,
                row.get("lot_size", 1), row.get("lots", 1),
            )
            prediction["gamma_source"] = "CSV snapshot"
        rows.append(score_forecast(prediction, data.iloc[index + 1].to_dict(),
                                   tolerance_pct=tolerance_pct))
    return pd.DataFrame(rows)


class CandleJournal:
    """First-write-wins forecasts: refreshes cannot revise old predictions."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS candle_forecasts (
                    instrument TEXT, interval TEXT, origin TEXT,
                    target TEXT, model TEXT, payload TEXT, status TEXT,
                    PRIMARY KEY(instrument, interval, origin, model)
                )
            """)

    def record_latest(self, instrument: str, interval: str, completed: pd.DataFrame,
                      *, minutes: int, window: int = 10,
                      option_gamma: float | None = None, lot_size: int = 1,
                      lots: int = 1, gamma_source: str = "unavailable",
                      tolerance_pct: float = 0.1) -> bool:
        if minutes < 1 or int(minutes) != minutes:
            raise ValueError("minutes must be a positive integer")
        if not isfinite(tolerance_pct) or tolerance_pct < 0:
            raise ValueError("Tolerance must be finite and nonnegative")
        prediction = forecast_next(completed, window=window)
        prediction.update(
            gamma_exposure=gamma_exposure(option_gamma, lot_size, lots),
            option_gamma=option_gamma, lot_size=lot_size, lots=lots,
            gamma_source=gamma_source, tolerance_pct=tolerance_pct,
            instrument=instrument, interval=interval, status="PENDING",
            recorded_at=pd.Timestamp.now(tz="UTC").isoformat(),
        )
        target = (pd.Timestamp(prediction["timestamp"]) + pd.Timedelta(minutes=minutes)).isoformat()
        prediction["target_timestamp"] = target
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO candle_forecasts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (instrument, interval, prediction["timestamp"], target, MODEL,
                 json.dumps(prediction, allow_nan=False), "PENDING"),
            )
            return cursor.rowcount > 0

    def evaluate(self, instrument: str, interval: str, completed: pd.DataFrame) -> int:
        data = validate_candles(completed)
        actuals = {pd.Timestamp(row["timestamp"]).isoformat(): row.to_dict()
                   for _, row in data.iterrows()}
        updated = 0
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT origin, target, model, payload FROM candle_forecasts "
                "WHERE instrument=? AND interval=? AND status='PENDING'",
                (instrument, interval),
            ).fetchall()
            for origin, target, model, payload in rows:
                if target not in actuals:
                    continue
                prediction = json.loads(payload)
                scored = score_forecast(prediction, actuals[target],
                                        tolerance_pct=prediction["tolerance_pct"])
                cursor = connection.execute(
                    "UPDATE candle_forecasts SET payload=?, status='SCORED' "
                    "WHERE instrument=? AND interval=? AND origin=? AND model=? AND status='PENDING'",
                    (json.dumps(scored, allow_nan=False), instrument, interval, origin, model),
                )
                updated += cursor.rowcount
        return updated

    def list_records(self, *, limit: int = 1000) -> pd.DataFrame:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT payload FROM candle_forecasts ORDER BY origin DESC LIMIT ?", (limit,),
            ).fetchall()
        return pd.DataFrame([json.loads(row[0]) for row in rows])
