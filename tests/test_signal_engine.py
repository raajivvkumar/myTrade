from __future__ import annotations

import pandas as pd
import pytest

from app.strategy.signal_engine import generate_chart_signals


def test_emits_buy_and_sell_only_on_crossovers() -> None:
    closes = [10, 9, 8, 9, 11, 12, 10, 8]
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(closes), freq="min"),
            "open": closes,
            "high": [price + 0.5 for price in closes],
            "low": [price - 0.5 for price in closes],
            "close": closes,
        }
    )

    result = generate_chart_signals(frame, fast_period=2, slow_period=3)

    assert result.loc[result["signal"] == "BUY", "timestamp"].tolist() == [
        pd.Timestamp("2026-01-01 00:04")
    ]
    assert result.loc[result["signal"] == "SELL", "timestamp"].tolist() == [
        pd.Timestamp("2026-01-01 00:06")
    ]
    assert result["signal"].iloc[0] == "HOLD"
    assert (result["strength_pct"] >= 0).all()


def test_rejects_invalid_periods_and_missing_columns() -> None:
    frame = pd.DataFrame({"timestamp": ["2026-01-01"], "close": [100]})
    with pytest.raises(ValueError, match="Periods"):
        generate_chart_signals(frame, fast_period=5, slow_period=5)
    with pytest.raises(ValueError, match="Missing required columns"):
        generate_chart_signals(pd.DataFrame({"close": [100]}))


def test_sorts_and_deduplicates_timestamps() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": ["2026-01-01 00:01", "2026-01-01 00:00", "2026-01-01 00:00"],
            "close": [102, 100, 101],
        }
    )
    result = generate_chart_signals(frame, fast_period=1, slow_period=2)
    assert result["timestamp"].is_monotonic_increasing
    assert result["timestamp"].is_unique
    assert result["close"].tolist() == [101, 102]
