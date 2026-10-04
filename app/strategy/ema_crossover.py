"""Reference EMA crossover strategy used to exercise the backtest engine."""

from __future__ import annotations

import pandas as pd


def generate_ema_crossover_signals(
    frame: pd.DataFrame,
    *,
    fast_period: int = 9,
    slow_period: int = 21,
    allow_short: bool = False,
) -> pd.DataFrame:
    """Return EMA values plus desired-position signals.

    A long position is desired when the fast EMA is above the slow EMA.
    If ``allow_short`` is enabled, a short position is desired when the fast
    EMA is below the slow EMA. Otherwise the strategy remains flat.

    Signals are based only on each completed candle's close. The backtesting
    engine is responsible for executing them on the next candle's open.
    """

    if fast_period <= 0 or slow_period <= 0:
        raise ValueError("EMA periods must be greater than zero")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be smaller than slow_period")
    if "close" not in frame.columns:
        raise ValueError("Input data must contain a close column")

    result = frame.copy()
    result["close"] = pd.to_numeric(result["close"], errors="coerce")

    result["ema_fast"] = result["close"].ewm(
        span=fast_period,
        adjust=False,
        min_periods=slow_period,
    ).mean()
    result["ema_slow"] = result["close"].ewm(
        span=slow_period,
        adjust=False,
        min_periods=slow_period,
    ).mean()

    result["signal"] = 0
    ready = result["ema_fast"].notna() & result["ema_slow"].notna()
    result.loc[ready & (result["ema_fast"] > result["ema_slow"]), "signal"] = 1

    if allow_short:
        result.loc[ready & (result["ema_fast"] < result["ema_slow"]), "signal"] = -1

    result["signal"] = result["signal"].astype(int)
    return result
