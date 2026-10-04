"""Local Streamlit dashboard for myTrade research, backtests, and live signals.

Run from the repository root:
    streamlit run app/dashboard.py --server.address 127.0.0.1
"""
from __future__ import annotations

import os
import queue
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.backtest.engine import BacktestConfig, run_backtest
from app.broker.angel_auth import connect_market_data
from app.broker.angel_historical import get_candles
from app.broker.angel_live import AngelLiveFeed
from app.config import Settings
from app.strategy.ema_crossover import generate_ema_crossover_signals
from app.strategy.signal_engine import generate_chart_signals


st.set_page_config(page_title="myTrade", page_icon="📈", layout="wide")
st.title("myTrade")
st.caption("Local market research, historical backtesting, and rule-based live signals.")
st.info(
    "Signals are technical research outputs, not financial advice or guaranteed outcomes. "
    "This dashboard does not place orders."
)


def _chart(frame: pd.DataFrame, *, title: str, show_signals: bool = True) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=frame["timestamp"],
            open=frame["open"],
            high=frame["high"],
            low=frame["low"],
            close=frame["close"],
            name="Price",
        )
    )
    if "ema_fast" in frame:
        figure.add_trace(go.Scatter(x=frame["timestamp"], y=frame["ema_fast"], name="Fast EMA"))
        figure.add_trace(go.Scatter(x=frame["timestamp"], y=frame["ema_slow"], name="Slow EMA"))
    if show_signals and "signal" in frame:
        buys = frame[frame["signal"] == "BUY"]
        sells = frame[frame["signal"] == "SELL"]
        figure.add_trace(go.Scatter(
            x=buys["timestamp"], y=buys["low"], mode="markers", name="BUY",
            marker={"symbol": "triangle-up", "size": 12, "color": "#16834a"},
        ))
        figure.add_trace(go.Scatter(
            x=sells["timestamp"], y=sells["high"], mode="markers", name="SELL",
            marker={"symbol": "triangle-down", "size": 12, "color": "#c43c35"},
        ))
    figure.update_layout(
        title=title, xaxis_title="Time", yaxis_title="Price", height=580,
        xaxis_rangeslider_visible=False, template="plotly_white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
    )
    return figure


def _clean_candles(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("CSV is missing columns: " + ", ".join(sorted(missing)))
    result = frame.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="coerce")
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = (
        result.dropna(subset=list(required))
        .drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    if len(result) < 3:
        raise ValueError("Provide at least three valid OHLC candles.")
    return result


def _live_tab() -> None:
    with st.form("live_settings"):
        left, right = st.columns(2)
        exchange = left.selectbox("Exchange segment", ["NSE", "NFO", "BSE", "BFO"])
        symbol_token = right.text_input("Angel One symbol token")
        interval = st.selectbox(
            "Chart candle interval",
            ["ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE"],
            index=1,
        )
        start = st.form_submit_button("Connect and load chart", type="primary")

    if start:
        if not symbol_token.strip():
            st.error("Enter the numeric Angel One symbol token.")
        else:
            try:
                with st.spinner("Authenticating and loading recent candles…"):
                    session = connect_market_data()
                    now = datetime.now()
                    candles = get_candles(
                        session.client,
                        exchange=exchange,
                        symbol_token=symbol_token.strip(),
                        interval=interval,
                        from_datetime=now - timedelta(days=2),
                        to_datetime=now,
                    )
                    if candles.empty:
                        raise ValueError("Angel One returned no historical candles.")
                    exchange_types = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4}
                    feed = AngelLiveFeed(
                        auth_token=session.auth_token,
                        api_key=os.environ["ANGEL_API_KEY"],
                        client_code=os.environ["ANGEL_CLIENT_CODE"],
                        feed_token=session.feed_token,
                        exchange_type=exchange_types[exchange],
                        symbol_token=symbol_token.strip(),
                    )
                    feed.start()
                st.session_state["live_feed"] = feed
                st.session_state["live_history"] = candles
                st.session_state["live_interval"] = interval
                st.session_state["live_instrument"] = f"{exchange}:{symbol_token.strip()}"
                st.success("Live market-data subscription started.")
            except Exception as exc:
                st.error(f"Could not start live market data: {exc}")

    feed = st.session_state.get("live_feed")
    if not feed:
        st.caption("Add Angel One credentials to .env, then connect a token to start.")
        return

    if st.button("Disconnect live feed"):
        feed.stop()
        st.session_state.pop("live_feed", None)
        st.rerun()

    @st.fragment(run_every="2s")
    def live_panel() -> None:
        current_feed = st.session_state.get("live_feed")
        history = st.session_state.get("live_history")
        if current_feed is None or history is None:
            return

        ticks = st.session_state.setdefault("live_ticks", [])
        while True:
            try:
                ticks.append(current_feed.ticks.get_nowait())
            except queue.Empty:
                break
        # Keep a bounded tick history for the chart while preserving the
        # broker's historical candle window.
        ticks = ticks[-20000:]
        st.session_state["live_ticks"] = ticks

        plot_data = history.copy()
        if ticks:
            tick_frame = pd.DataFrame(ticks)
            tick_frame["timestamp"] = pd.to_datetime(tick_frame["timestamp"], utc=True)
            tick_frame["timestamp"] = (
                tick_frame["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            )
            rule = {"ONE_MINUTE": "1min", "FIVE_MINUTE": "5min", "FIFTEEN_MINUTE": "15min"}[
                st.session_state["live_interval"]
            ]
            tick_candles = (
                tick_frame.set_index("timestamp")["price"]
                .resample(rule)
                .ohlc()
                .dropna()
                .reset_index()
            )
            tick_candles["volume"] = 0
            # A tick-built candle replaces the broker candle for that timestamp.
            plot_data = pd.concat([plot_data, tick_candles], ignore_index=True)
            plot_data = (
                plot_data.drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .tail(500)
                .reset_index(drop=True)
            )

        # Exclude the active interval from signals; retain it in the chart.
        interval_rule = {"ONE_MINUTE": "1min", "FIVE_MINUTE": "5min", "FIFTEEN_MINUTE": "15min"}[
            st.session_state["live_interval"]
        ]
        active_bar_start = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).floor(interval_rule)
        completed = plot_data[plot_data["timestamp"] < active_bar_start].copy()
        instrument = st.session_state.get("live_instrument", "Live instrument")
        if len(completed) >= 3:
            signalled = generate_chart_signals(completed)
            last = signalled.iloc[-1]
            col1, col2, col3 = st.columns(3)
            col1.metric("Latest closed-candle signal", last["signal"])
            col2.metric("Last closed price", f'{float(last["close"]):,.2f}')
            col3.metric("EMA spread strength", f'{float(last["strength_pct"]):.3f}%')
            st.caption(
                f"BUY/SELL markers appear only on EMA crossovers. Current feed: "
                f'{"connected" if current_feed.connected else "disconnected"}.'
            )
            chart_frame = plot_data.merge(
                signalled[["timestamp", "ema_fast", "ema_slow", "signal"]],
                on="timestamp", how="left",
            )
            chart_frame["signal"] = chart_frame["signal"].fillna("HOLD")
            chart = _chart(chart_frame, title=f"{instrument} • live EMA signals")
        else:
            st.info("Waiting for enough completed candles to calculate a crossover.")
            chart = _chart(plot_data, title=f"{instrument} • live candles", show_signals=False)

        st.plotly_chart(chart, use_container_width=True)
        if ticks:
            st.caption(f"Latest live price: {float(ticks[-1]['price']):,.2f}")
        if not current_feed.connected:
            st.warning("The WebSocket is disconnected. Reconnect to resume live updates.")

    live_panel()


def _backtest_tab() -> None:
    st.write("Upload historical OHLC candles as CSV, or run the CLI against a local Parquet file.")
    uploaded = st.file_uploader("OHLC CSV", type=["csv"], key="backtest_csv")
    if uploaded is None:
        st.code(
            "python -m pip install -r requirements.txt\n"
            "python main.py backtest --input data/raw/NSE/99926000/FIVE_MINUTE.parquet "
            "--fast 9 --slow 21 --capital 100000 --quantity 1",
            language="bash",
        )
        return

    try:
        candles = _clean_candles(pd.read_csv(uploaded))
    except Exception as exc:
        st.error(f"Could not read candle CSV: {exc}")
        return

    with st.form("backtest_settings"):
        a, b, c = st.columns(3)
        fast = a.number_input("Fast EMA", min_value=1, max_value=200, value=9)
        slow = b.number_input("Slow EMA", min_value=2, max_value=500, value=21)
        capital = c.number_input("Initial capital", min_value=1.0, value=100000.0, step=10000.0)
        d, e, f = st.columns(3)
        quantity = d.number_input("Quantity", min_value=0.01, value=1.0, step=1.0)
        fee = e.number_input("Fee per order", min_value=0.0, value=0.0, step=1.0)
        slippage = f.number_input("Slippage (basis points)", min_value=0.0, value=0.0, step=1.0)
        allow_short = st.checkbox("Allow short positions", value=False)
        run = st.form_submit_button("Run backtest", type="primary")

    if int(fast) >= int(slow):
        st.warning("Fast EMA must be shorter than Slow EMA.")
        return
    if not run:
        st.caption(f"Loaded {len(candles):,} candles.")
        return

    strategy_frame = generate_ema_crossover_signals(
        candles, fast_period=int(fast), slow_period=int(slow), allow_short=allow_short
    )
    result = run_backtest(
        candles,
        strategy_frame["signal"],
        BacktestConfig(
            initial_capital=float(capital),
            quantity=float(quantity),
            fee_per_order=float(fee),
            slippage_bps=float(slippage),
            allow_short=allow_short,
        ),
    )
    metrics = result.metrics
    top = st.columns(4)
    top[0].metric("Net P&L", f"₹{metrics['net_pnl']:,.2f}")
    top[1].metric("Return", f"{metrics['total_return_pct']:.2f}%")
    top[2].metric("Max drawdown", f"{metrics['max_drawdown_pct']:.2f}%")
    top[3].metric("Win rate", f"{metrics['win_rate_pct']:.2f}%")
    lower = st.columns(4)
    lower[0].metric("Trades", metrics["trades"])
    lower[1].metric("Profit factor", str(metrics["profit_factor"]))
    lower[2].metric("Average trade", f"₹{metrics['average_trade']:,.2f}")
    lower[3].metric("Max losing streak", metrics["max_consecutive_losses"])

    equity = result.equity_curve
    equity_fig = go.Figure(go.Scatter(
        x=equity["timestamp"], y=equity["equity"], mode="lines", name="Equity"
    ))
    equity_fig.update_layout(title="Backtest equity curve", height=340, template="plotly_white")
    st.plotly_chart(equity_fig, use_container_width=True)

    signals = generate_chart_signals(candles, fast_period=int(fast), slow_period=int(slow))
    st.plotly_chart(
        _chart(signals, title="EMA strategy signals"),
        use_container_width=True,
    )
    st.subheader("Trades")
    st.dataframe(result.trades_frame(), use_container_width=True, hide_index=True)
    st.download_button(
        "Download trades CSV",
        result.trades_frame().to_csv(index=False).encode("utf-8"),
        file_name="mytrade_trades.csv",
        mime="text/csv",
    )


live_tab, backtest_tab = st.tabs(["Live chart and signals", "Backtesting"])
with live_tab:
    _live_tab()
with backtest_tab:
    _backtest_tab()
