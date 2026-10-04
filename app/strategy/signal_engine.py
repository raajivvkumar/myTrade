"""Closed-candle EMA crossover signals for charts and backtests.

The module is deliberately deterministic and has no AI/API dependency. Callers
must pass completed candles when they need signals that do not repaint.
"""
from __future__ import annotations

import pandas as pd


def generate_chart_signals(
    frame: pd.DataFrame,
    *,
    fast_period: int = 9,
    slow_period: int = 21,
) -> pd.DataFrame:
    """Add EMA values, crossover event labels, and a normalized spread score.

    BUY and SELL are emitted only on an EMA crossover. All other rows are HOLD.
    The score is the absolute EMA spread as a percentage of closing price; it is
    a descriptive trend-strength measure, not a probability of profit.
    """
    if fast_period < 1 or slow_period < 2 or fast_period >= slow_period:
        raise ValueError("Periods must satisfy 1 <= fast_period < slow_period")
    missing = {"timestamp", "close"}.difference(frame.columns)
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(sorted(missing)))

    result = frame.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = (
        result.dropna(subset=["timestamp", "close"])
        .drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    if (result["close"] <= 0).any():
        raise ValueError("close prices must be greater than zero")

    result["ema_fast"] = result["close"].ewm(
        span=fast_period, adjust=False, min_periods=slow_period
    ).mean()
    result["ema_slow"] = result["close"].ewm(
        span=slow_period, adjust=False, min_periods=slow_period
    ).mean()

    spread = result["ema_fast"] - result["ema_slow"]
    previous = spread.shift(1)
    ready = spread.notna() & previous.notna()
    crossed_up = ready & (spread > 0) & (previous <= 0)
    crossed_down = ready & (spread < 0) & (previous >= 0)

    result["signal"] = "HOLD"
    result.loc[crossed_up, "signal"] = "BUY"
    result.loc[crossed_down, "signal"] = "SELL"
    result["position"] = (spread > 0).astype(int)
    result.loc[spread.isna(), "position"] = 0
    result["strength_pct"] = (spread.abs() / result["close"] * 100).fillna(0.0)
    return result
