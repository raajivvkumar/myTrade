from __future__ import annotations

import pandas as pd
import pytest

from app.backtest.engine import BacktestConfig, run_backtest
from app.strategy.ema_crossover import generate_ema_crossover_signals


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01 09:15", periods=5, freq="5min"),
            "open": [100.0, 101.0, 103.0, 104.0, 105.0],
            "high": [101.0, 104.0, 105.0, 106.0, 106.0],
            "low": [99.0, 100.0, 102.0, 103.0, 104.0],
            "close": [100.0, 103.0, 104.0, 105.0, 105.0],
            "volume": [10, 10, 10, 10, 10],
        }
    )


def test_backtest_executes_signal_on_next_bar_open() -> None:
    frame = sample_frame()
    signals = pd.Series([1, 1, 0, 0, 0])
    config = BacktestConfig(
        initial_capital=1000.0,
        quantity=1.0,
        fee_per_order=1.0,
        slippage_bps=0.0,
    )

    result = run_backtest(frame, signals, config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(101.0)
    assert trade.exit_price == pytest.approx(104.0)
    assert trade.gross_pnl == pytest.approx(3.0)
    assert trade.fees == pytest.approx(2.0)
    assert trade.net_pnl == pytest.approx(1.0)
    assert result.metrics["final_equity"] == pytest.approx(1001.0)


def test_short_signal_requires_explicit_permission() -> None:
    frame = sample_frame()
    signals = pd.Series([-1, -1, 0, 0, 0])

    with pytest.raises(ValueError, match="allow_short"):
        run_backtest(frame, signals, BacktestConfig(allow_short=False))


def test_slippage_reduces_long_trade_profit() -> None:
    frame = sample_frame()
    signals = pd.Series([1, 1, 0, 0, 0])

    without_slippage = run_backtest(
        frame,
        signals,
        BacktestConfig(initial_capital=1000.0, slippage_bps=0.0),
    )
    with_slippage = run_backtest(
        frame,
        signals,
        BacktestConfig(initial_capital=1000.0, slippage_bps=10.0),
    )

    assert with_slippage.metrics["net_pnl"] < without_slippage.metrics["net_pnl"]


def test_ema_strategy_waits_for_slow_period_before_signalling() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01 09:15", periods=8, freq="5min"),
            "open": [100, 101, 102, 103, 104, 105, 106, 107],
            "close": [100, 101, 102, 103, 104, 105, 106, 107],
        }
    )

    strategy = generate_ema_crossover_signals(
        frame,
        fast_period=2,
        slow_period=4,
    )

    assert strategy.loc[:2, "signal"].tolist() == [0, 0, 0]
    assert strategy.loc[3:, "signal"].eq(1).all()
