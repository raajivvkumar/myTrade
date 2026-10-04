"""Core deterministic backtesting engine for myTrade.

Signals represent the desired position after each candle closes:

    1  = long
    0  = flat
   -1  = short

To avoid look-ahead bias, a signal produced on candle N is executed at the
OPEN of candle N+1. The engine supports fixed per-order fees and configurable
slippage in basis points. It intentionally does not place live orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import inf
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = {"timestamp", "open", "close"}


@dataclass(frozen=True)
class BacktestConfig:
    """Execution and account assumptions for a historical simulation."""

    initial_capital: float = 100_000.0
    quantity: float = 1.0
    fee_per_order: float = 0.0
    slippage_bps: float = 0.0
    allow_short: bool = False

    def validate(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be greater than zero")
        if self.quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        if self.fee_per_order < 0:
            raise ValueError("fee_per_order cannot be negative")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps cannot be negative")


@dataclass(frozen=True)
class Trade:
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    bars_held: int
    exit_reason: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entry_time"] = self.entry_time.isoformat()
        data["exit_time"] = self.exit_time.isoformat()
        return data


@dataclass(frozen=True)
class BacktestResult:
    config: BacktestConfig
    metrics: dict[str, Any]
    trades: tuple[Trade, ...]
    equity_curve: pd.DataFrame

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([trade.to_dict() for trade in self.trades])


def _apply_slippage(price: float, order_side: str, slippage_bps: float) -> float:
    rate = slippage_bps / 10_000.0
    if order_side == "buy":
        return price * (1.0 + rate)
    if order_side == "sell":
        return price * (1.0 - rate)
    raise ValueError(f"Unsupported order side: {order_side}")


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(
            "Backtest data is missing required columns: " + ", ".join(sorted(missing))
        )
    if len(frame) < 2:
        raise ValueError("Backtesting requires at least two candles")

    prepared = frame.copy()
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], errors="coerce")
    for column in ("open", "close"):
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

    prepared = (
        prepared.dropna(subset=["timestamp", "open", "close"])
        .drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    if len(prepared) < 2:
        raise ValueError("Not enough valid candles remain after cleaning")
    if (prepared[["open", "close"]] <= 0).any().any():
        raise ValueError("open and close prices must be greater than zero")
    return prepared


def _prepare_signals(
    signals: pd.Series | list[int],
    expected_length: int,
    *,
    allow_short: bool,
) -> pd.Series:
    signal_series = pd.Series(signals).reset_index(drop=True)
    if len(signal_series) != expected_length:
        raise ValueError(
            f"Expected {expected_length} signals, received {len(signal_series)}"
        )

    signal_series = pd.to_numeric(signal_series, errors="coerce").fillna(0).astype(int)
    invalid = ~signal_series.isin((-1, 0, 1))
    if invalid.any():
        bad = sorted(set(signal_series[invalid].tolist()))
        raise ValueError(f"Signals must contain only -1, 0 or 1; received {bad}")
    if not allow_short and (signal_series < 0).any():
        raise ValueError("Short signals were supplied but allow_short is False")
    return signal_series


def _calculate_metrics(
    equity_curve: pd.DataFrame,
    trades: list[Trade],
    initial_capital: float,
    frame: pd.DataFrame,
) -> dict[str, Any]:
    final_equity = float(equity_curve.iloc[-1]["equity"])
    net_pnl = final_equity - initial_capital
    total_return_pct = (net_pnl / initial_capital) * 100.0

    equity = equity_curve["equity"].astype(float)
    running_peak = equity.cummax()
    drawdown = (equity / running_peak) - 1.0
    max_drawdown_pct = max(0.0, float(-drawdown.min() * 100.0))

    net_results = [trade.net_pnl for trade in trades]
    wins = [value for value in net_results if value > 0]
    losses = [value for value in net_results if value < 0]

    gross_profit = float(sum(wins))
    gross_loss_abs = float(abs(sum(losses)))
    if gross_loss_abs > 0:
        profit_factor: float | None = gross_profit / gross_loss_abs
    elif gross_profit > 0:
        profit_factor = inf
    else:
        profit_factor = None

    trade_count = len(trades)
    win_rate = (len(wins) / trade_count * 100.0) if trade_count else 0.0
    average_trade = (sum(net_results) / trade_count) if trade_count else 0.0
    average_win = (sum(wins) / len(wins)) if wins else 0.0
    average_loss = (sum(losses) / len(losses)) if losses else 0.0

    max_consecutive_losses = 0
    current_losses = 0
    for value in net_results:
        if value < 0:
            current_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
        else:
            current_losses = 0

    benchmark_return_pct = (
        (float(frame.iloc[-1]["close"]) / float(frame.iloc[0]["close"])) - 1.0
    ) * 100.0

    return {
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final_equity, 2),
        "net_pnl": round(net_pnl, 2),
        "total_return_pct": round(total_return_pct, 4),
        "benchmark_buy_hold_pct": round(benchmark_return_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "trades": trade_count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 4),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss_abs, 2),
        "profit_factor": None if profit_factor is None else (
            "inf" if profit_factor == inf else round(profit_factor, 4)
        ),
        "average_trade": round(float(average_trade), 2),
        "average_win": round(float(average_win), 2),
        "average_loss": round(float(average_loss), 2),
        "max_consecutive_losses": max_consecutive_losses,
    }


def run_backtest(
    frame: pd.DataFrame,
    signals: pd.Series | list[int],
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run a next-bar-open historical simulation.

    The strategy creates a desired-position signal after each candle close.
    This function executes that decision on the following candle's open,
    preventing the strategy from trading on information that was not yet
    available at execution time.
    """

    config = config or BacktestConfig()
    config.validate()
    data = _prepare_frame(frame)
    signal_series = _prepare_signals(
        signals,
        len(data),
        allow_short=config.allow_short,
    )

    position = 0
    entry_price = 0.0
    entry_time: pd.Timestamp | None = None
    entry_index: int | None = None
    realized_pnl = 0.0
    trades: list[Trade] = []
    equity_rows: list[dict[str, Any]] = []

    def close_position(
        *,
        raw_price: float,
        timestamp: pd.Timestamp,
        index: int,
        reason: str,
    ) -> None:
        nonlocal position, entry_price, entry_time, entry_index, realized_pnl
        if position == 0 or entry_time is None or entry_index is None:
            return

        order_side = "sell" if position == 1 else "buy"
        exit_price = _apply_slippage(raw_price, order_side, config.slippage_bps)
        gross_pnl = position * (exit_price - entry_price) * config.quantity
        realized_pnl += gross_pnl - config.fee_per_order
        fees = config.fee_per_order * 2.0
        net_pnl = gross_pnl - fees
        return_pct = (
            position * ((exit_price - entry_price) / entry_price) * 100.0
        )
        trades.append(
            Trade(
                side="LONG" if position == 1 else "SHORT",
                entry_time=entry_time,
                exit_time=timestamp,
                entry_price=round(entry_price, 6),
                exit_price=round(exit_price, 6),
                quantity=config.quantity,
                gross_pnl=round(gross_pnl, 6),
                fees=round(fees, 6),
                net_pnl=round(net_pnl, 6),
                return_pct=round(return_pct, 6),
                bars_held=index - entry_index,
                exit_reason=reason,
            )
        )
        position = 0
        entry_price = 0.0
        entry_time = None
        entry_index = None

    def open_position(
        *,
        target: int,
        raw_price: float,
        timestamp: pd.Timestamp,
        index: int,
    ) -> None:
        nonlocal position, entry_price, entry_time, entry_index, realized_pnl
        if target == 0:
            return
        order_side = "buy" if target == 1 else "sell"
        entry_price = _apply_slippage(raw_price, order_side, config.slippage_bps)
        realized_pnl -= config.fee_per_order
        position = target
        entry_time = timestamp
        entry_index = index

    # No trade occurs on candle 0. Its signal can only be acted on at candle 1.
    equity_rows.append(
        {
            "timestamp": data.iloc[0]["timestamp"],
            "equity": config.initial_capital,
            "close": float(data.iloc[0]["close"]),
            "position": 0,
        }
    )

    for index in range(1, len(data)):
        row = data.iloc[index]
        timestamp = pd.Timestamp(row["timestamp"])
        raw_open = float(row["open"])
        target = int(signal_series.iloc[index - 1])

        if target != position:
            if position != 0:
                close_position(
                    raw_price=raw_open,
                    timestamp=timestamp,
                    index=index,
                    reason="signal_change",
                )
            if target != 0:
                open_position(
                    target=target,
                    raw_price=raw_open,
                    timestamp=timestamp,
                    index=index,
                )

        close_price = float(row["close"])
        unrealized_pnl = (
            position * (close_price - entry_price) * config.quantity
            if position != 0
            else 0.0
        )
        equity_rows.append(
            {
                "timestamp": timestamp,
                "equity": config.initial_capital + realized_pnl + unrealized_pnl,
                "close": close_price,
                "position": position,
            }
        )

    # Force-close any remaining position on the final candle close so every
    # backtest ends flat and final equity includes exit slippage/fees.
    if position != 0:
        last_index = len(data) - 1
        last_row = data.iloc[last_index]
        close_position(
            raw_price=float(last_row["close"]),
            timestamp=pd.Timestamp(last_row["timestamp"]),
            index=last_index,
            reason="end_of_data",
        )
        equity_rows[-1]["equity"] = config.initial_capital + realized_pnl
        equity_rows[-1]["position"] = 0

    equity_curve = pd.DataFrame(equity_rows)
    metrics = _calculate_metrics(
        equity_curve=equity_curve,
        trades=trades,
        initial_capital=config.initial_capital,
        frame=data,
    )

    return BacktestResult(
        config=config,
        metrics=metrics,
        trades=tuple(trades),
        equity_curve=equity_curve,
    )
