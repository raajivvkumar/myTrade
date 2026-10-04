from __future__ import annotations

import pandas as pd

from app.backtest.engine import BacktestConfig, run_backtest


def test_simulated_lots_and_order_charges_change_trade_pnl() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=3, freq="min"),
            "open": [100.0, 100.0, 110.0],
            "close": [100.0, 105.0, 110.0],
        }
    )
    result = run_backtest(
        frame,
        [1, 1, 0],
        BacktestConfig(
            initial_capital=1000,
            lots=2,
            lot_size=10,
            fee_per_order=20,
            extra_charge_per_lot_order=2,
        ),
    )

    trade = result.trades[0]
    assert trade.quantity == 20
    assert trade.gross_pnl == 200
    assert trade.fees == 48
    assert trade.net_pnl == 152
    assert result.metrics["total_charges"] == 48
    assert result.metrics["final_equity"] == 1152
