"""Local Streamlit dashboard for myTrade research, backtests, and live signals.

Run from the repository root:
    streamlit run app/dashboard.py --server.address 127.0.0.1
"""
from __future__ import annotations

import os
import json
import queue
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.backtest.engine import BacktestConfig, run_backtest
from app.broker.focus_universe import FOCUS_MARKETS, focus_instruments, instrument_label
from app.broker.instruments import load_instruments
from app.config import Settings
from app.data.archive import contract_id
from app.data.archive_ui import archive, select_history, history_tab
from app.data.storage import load_candles_parquet, save_candles_parquet
from app.review.prediction_journal import PredictionJournal
from app.review.accuracy_ui import accuracy_tab as render_accuracy_tab, journal as candle_journal, style_gamma
from app.review.candle_accuracy import gamma_exposure
from app.strategy.ema_crossover import generate_ema_crossover_signals
from app.strategy.signal_engine import generate_chart_signals


st.set_page_config(page_title="myTrade", page_icon="📈", layout="wide")
st.title("myTrade")
st.sidebar.number_input(
    "High gamma exposure threshold", min_value=0.000001, value=0.01, format="%.6f",
    key="gamma_threshold",
    help="Option gamma × lot size × lots. Compare the same underlying and Greek units."
)
st.caption("Local market research, historical backtesting, and rule-based live signals.")
st.info(
    "Signals are technical research outputs, not financial advice or guaranteed outcomes. "
    "This dashboard does not place orders."
)



def _local_timestamps(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return parsed


def _catalogue(focus: str) -> list[dict]:
    settings = Settings.from_env()
    settings.ensure_local_directories()
    path = settings.data_dir / "reference" / "angel_instruments.json"
    # Reading saved history must not require network access or an active broker.
    instruments = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return focus_instruments(instruments, focus)


def _journal() -> PredictionJournal:
    settings = Settings.from_env()
    return PredictionJournal(settings.data_dir / "mytrade_journal.sqlite3", horizon_bars=3)


def _review_tab() -> None:
    journal = _journal()
    predictions = journal.list_predictions()
    st.subheader("Prediction history and review")
    st.caption(
        "Every BUY/SELL crossover is stored locally and checked against the close "
        "three completed candles later. Use both questions to review the result."
    )
    if predictions.empty:
        st.info("No live signals have been recorded yet. Start a live chart to build this history.")
        return
    scored = predictions[predictions["status"].isin(["PASSED", "FAILED"])]
    passed = int((scored["status"] == "PASSED").sum())
    failed = int((scored["status"] == "FAILED").sum())
    summary = st.columns(4)
    summary[0].metric("Signals recorded", len(predictions))
    summary[1].metric("Passed", passed)
    summary[2].metric("Failed", failed)
    summary[3].metric(
        "Directional accuracy",
        f"{passed / len(scored) * 100:.1f}%" if len(scored) else "Pending",
    )
    columns = [
        "id", "instrument", "interval", "timestamp", "signal", "status",
        "entry_close", "outcome_close", "outcome_return_pct", "outcome_reason",
        "review_why_failed", "review_why_passed", "review_note", "gamma_exposure", "gamma_source",
    ]
    st.dataframe(style_gamma(predictions[columns]), use_container_width=True, hide_index=True)
    choices = predictions["id"].astype(int).tolist()
    selected_id = st.selectbox("Signal to review", choices)
    selected = predictions[predictions["id"] == selected_id].iloc[0]
    st.write(
        f"**{selected['instrument']} · {selected['signal']} · {selected['status']}** "
        f"— {selected['outcome_reason'] or 'Outcome pending'}"
    )
    if selected["status"] == "PENDING":
        st.info("This signal will be scored after three more completed candles.")
        return
    st.markdown("#### Always ask: Why did I fail? Why did I pass?")
    with st.form("prediction_review"):
        why_failed = st.text_area("Why did I fail?", value=selected["review_why_failed"] or "")
        why_passed = st.text_area("Why did I pass?", value=selected["review_why_passed"] or "")
        note = st.text_area(
            "What should I change or keep for the next signal?",
            value=selected["review_note"] or "",
        )
        save = st.form_submit_button("Save review")
    if save:
        journal.save_review(
            int(selected_id),
            why_failed=why_failed,
            why_passed=why_passed,
            note=note,
        )
        st.success("Review saved to the local prediction journal.")


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
    if "gamma_exposure" in frame:
        high_gamma = frame[
            pd.to_numeric(frame["gamma_exposure"], errors="coerce").ge(st.session_state["gamma_threshold"])
            & frame["signal"].isin(["BUY", "SELL"])
        ]
        figure.add_trace(go.Scatter(
            x=high_gamma["timestamp"], y=high_gamma["close"], mode="markers",
            name="High gamma exposure", marker={"symbol": "diamond", "size": 15, "color": "#d4a017"},
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
    result["timestamp"] = _local_timestamps(result["timestamp"])
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
    if st.button("Refresh current broker catalogue"):
        try:
            load_instruments(Settings.from_env().data_dir / "reference" / "angel_instruments.json", force_refresh=True)
        except Exception as exc:
            st.error(f"Catalogue refresh failed: {exc}")
    with st.form("live_settings"):
        focus = st.selectbox("Market group", FOCUS_MARKETS)
        try:
            catalogue = _catalogue(focus)
        except Exception as exc:
            catalogue = []
            st.warning(f"Could not load Angel One's instrument master: {exc}")
        selected_instrument = st.selectbox(
            "Instrument (current Angel One master)",
            catalogue,
            format_func=instrument_label,
            index=None if not catalogue else 0,
            key="live_instrument_choice",
        )
        interval = st.selectbox(
            "Chart candle interval",
            ["ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE"],
            index=1,
        )
        start = st.form_submit_button("Connect and load chart", type="primary")

    if start:
        if not selected_instrument:
            st.error("Select a supported instrument from the current Angel One master.")
        else:
            try:
                with st.spinner("Authenticating and loading recent candles…"):
                    from app.broker.angel_auth import connect_market_data
                    from app.broker.angel_historical import get_candles
                    from app.broker.angel_live import AngelLiveFeed
                    session = connect_market_data()
                    now = datetime.now()
                    exchange = str(selected_instrument["exch_seg"]).upper()
                    symbol_token = str(selected_instrument["token"])
                    candles = get_candles(
                        session.client,
                        exchange=exchange,
                        symbol_token=symbol_token,
                        interval=interval,
                        from_datetime=now - timedelta(days=2),
                        to_datetime=now,
                    )
                    if candles.empty:
                        raise ValueError("Angel One returned no historical candles.")
                    candles["timestamp"] = _local_timestamps(candles["timestamp"])
                    exchange_types = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4, "MCX": 5}
                    feed = AngelLiveFeed(
                        auth_token=session.auth_token,
                        api_key=os.environ["ANGEL_API_KEY"],
                        client_code=os.environ["ANGEL_CLIENT_CODE"],
                        feed_token=session.feed_token,
                        exchange_type=exchange_types[exchange],
                        symbol_token=symbol_token,
                    )
                    archive().save(selected_instrument, interval, candles, source="angel-historical")
                    feed.start()
                    history_path = (
                        Settings.from_env().data_dir / "live" / exchange / contract_id(selected_instrument)
                        / f"{interval}.parquet"
                    )
                    save_candles_parquet(candles, history_path)
                st.session_state["live_contract_metadata"] = dict(selected_instrument)
                st.session_state["live_feed"] = feed
                st.session_state["live_history_path"] = str(history_path)
                st.session_state["live_interval"] = interval
                st.session_state["live_instrument"] = (
                    f"{focus} · {selected_instrument.get('symbol') or selected_instrument.get('name')} "
                    f"({exchange}:{symbol_token})"
                )
                st.session_state["live_lot_size"] = int(selected_instrument.get("lotsize") or 1)
                st.session_state["live_is_option"] = "OPT" in str(selected_instrument.get("instrumenttype", "")).upper()
                st.session_state.pop("gamma_snapshot", None)
                st.session_state["live_started_at"] = datetime.now()
                st.session_state.pop("live_ticks", None)
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

    if st.session_state.get("live_is_option"):
        with st.form("gamma_snapshot"):
            st.caption("Enter actual broker option gamma for this contract. Snapshot expires after 5 minutes.")
            option_gamma = st.number_input("Option gamma", min_value=0.0, value=0.0, format="%.6f")
            gamma_lots = st.number_input("Position lots for gamma exposure", min_value=1, value=1)
            gamma_save = st.form_submit_button("Save current broker gamma snapshot")
        if gamma_save:
            st.session_state["gamma_snapshot"] = {
                "value": float(option_gamma), "lots": int(gamma_lots),
                "captured_at": pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None),
            }
            st.success("Manual broker gamma saved for the selected contract.")

    @st.fragment(run_every="2s")
    def live_panel() -> None:
        current_feed = st.session_state.get("live_feed")
        history_path = st.session_state.get("live_history_path")
        if current_feed is None or not history_path:
            return
        history_path = Path(history_path)

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

        plot_data = load_candles_parquet(history_path)
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
            # The tick feed does not provide reliable per-bar volume here.
            archived_ticks = tick_candles.copy()
            archived_ticks["volume"] = None
            archived_ticks["quality"] = "partial tick reconstruction; volume unavailable"
            try:
                archive().save(st.session_state["live_contract_metadata"],
                               st.session_state["live_interval"], archived_ticks.tail(3),
                               source="angel-live-ticks")
            except Exception as exc:
                st.error(f"History archive write failed: {exc}")
            # Persist every live interval, including the still-forming candle.
            save_candles_parquet(tick_candles.tail(2), history_path)
            # The parquet retains all bars; only the chart view is shortened below.
            plot_data = load_candles_parquet(history_path)
            plot_data["timestamp"] = _local_timestamps(plot_data["timestamp"])
            plot_data = (
                plot_data.drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
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
            journal = _journal()
            live_start = pd.Timestamp(st.session_state.get("live_started_at", datetime.now()))
            new_live_signals = signalled[signalled["timestamp"] >= live_start]
            snapshot = st.session_state.get("gamma_snapshot")
            gamma_value = None
            gamma_lots = 1
            gamma_source = "unavailable"
            now_local = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
            if snapshot and now_local - snapshot["captured_at"] <= pd.Timedelta(minutes=5):
                gamma_value = snapshot["value"]
                gamma_lots = snapshot["lots"]
                gamma_source = "Manual broker snapshot at " + snapshot["captured_at"].isoformat()
            exposure = gamma_exposure(gamma_value, st.session_state.get("live_lot_size", 1), gamma_lots)
            new_live_signals = new_live_signals.copy()
            # Apply the snapshot only to a recent completed signal candle.
            new_live_signals["gamma_exposure"] = None
            new_live_signals["gamma_source"] = "unavailable"
            recent = new_live_signals["timestamp"] >= active_bar_start - pd.Timedelta(minutes=5)
            new_live_signals.loc[recent, "gamma_exposure"] = exposure
            new_live_signals.loc[recent, "gamma_source"] = gamma_source
            journal.record_signals(
                instrument, st.session_state["live_interval"], new_live_signals
            )
            journal.evaluate_matured(instrument, st.session_state["live_interval"], completed)
            forecasts = candle_journal()
            forecasts.evaluate(instrument, st.session_state["live_interval"], completed)
            minutes = {"ONE_MINUTE": 1, "FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15}[st.session_state["live_interval"]]
            origin = pd.Timestamp(completed.iloc[-1]["timestamp"])
            target = origin + pd.Timedelta(minutes=minutes)
            # Save only a still-forming target; never backfill live predictions.
            if len(completed) >= 11 and target == active_bar_start:
                forecasts.record_latest(
                    instrument, st.session_state["live_interval"], completed, minutes=minutes,
                    option_gamma=gamma_value, lot_size=st.session_state.get("live_lot_size", 1),
                    lots=gamma_lots, gamma_source=gamma_source,
                )
            st.caption("Gamma source: " + gamma_source)
            last = signalled.iloc[-1]
            col1, col2, col3 = st.columns(3)
            col1.metric("Latest closed-candle signal", last["signal"])
            col2.metric("Last closed price", f'{float(last["close"]):,.2f}')
            col3.metric("EMA spread strength", f'{float(last["strength_pct"]):.3f}%')
            st.caption(
                f"BUY/SELL markers appear only on EMA crossovers. Current feed: "
                f'{"connected" if current_feed.connected else "disconnected"}.'
            )
            chart_frame = plot_data.tail(500).merge(
                signalled.tail(500)[["timestamp", "ema_fast", "ema_slow", "signal"]],
                on="timestamp", how="left",
            )
            chart_frame["signal"] = chart_frame["signal"].fillna("HOLD")
            saved_signals = journal.list_predictions(limit=5000)
            if not saved_signals.empty:
                gamma_rows = saved_signals[
                    (saved_signals["instrument"] == instrument) &
                    (saved_signals["interval"] == st.session_state["live_interval"])
                ][["timestamp", "gamma_exposure"]].copy()
                gamma_rows["timestamp"] = pd.to_datetime(gamma_rows["timestamp"])
                chart_frame = chart_frame.merge(gamma_rows.drop_duplicates("timestamp"), on="timestamp", how="left")
            chart_frame = chart_frame.tail(500)
            chart = _chart(chart_frame, title=f"{instrument} • live EMA signals")
        else:
            st.info("Waiting for enough completed candles to calculate a crossover.")
            chart = _chart(plot_data, title=f"{instrument} • live candles", show_signals=False)

        stored = candle_journal().list_records()
        if not stored.empty:
            matching = stored[
                (stored["instrument"] == instrument) &
                (stored["interval"] == st.session_state["live_interval"]) &
                (stored["target_timestamp"] == active_bar_start.isoformat())
            ]
            if not matching.empty:
                prediction = matching.iloc[0]
                exposure_value = prediction.get("gamma_exposure")
                high_gamma = pd.notna(exposure_value) and float(exposure_value) >= st.session_state["gamma_threshold"]
                forecast_color = "#d4a017" if high_gamma else "#3979c6"
                chart.add_trace(go.Candlestick(
                    x=[pd.Timestamp(prediction["target_timestamp"])],
                    open=[prediction["predicted_open"]], high=[prediction["predicted_high"]],
                    low=[prediction["predicted_low"]], close=[prediction["predicted_close"]],
                    name="Experimental predicted candle",
                    increasing_line_color=forecast_color, decreasing_line_color=forecast_color,
                    increasing_fillcolor="rgba(57,121,198,0.15)",
                    decreasing_fillcolor="rgba(57,121,198,0.15)",
                ))
                st.metric("Experimental next candle close", f'{prediction["predicted_close"]:,.4f}')
        st.plotly_chart(chart, use_container_width=True)
        st.caption(f"Full candle history is being appended locally at {history_path}.")
        if ticks:
            st.caption(f"Latest live price: {float(ticks[-1]['price']):,.2f}")
        if not current_feed.connected:
            st.warning("The WebSocket is disconnected. Reconnect to resume live updates.")

    live_panel()


def _backtest_tab() -> None:
    st.write(
        "Backtest NIFTY 50, MIDCPNIFTY, BANKNIFTY, or MCX history. "
        "You can upload candles, choose chart history already collected, or download a date range."
    )
    source = st.selectbox(
        "Historical data source",
        ["Upload OHLC CSV", "Permanent contract archive", "Saved local history", "Download from Angel One"],
    )
    candles = None
    selected_lot_size = 1
    source_label = ""

    if source == "Upload OHLC CSV":
        uploaded = st.file_uploader("OHLC CSV", type=["csv"], key="backtest_csv")
        if uploaded is not None:
            try:
                candles = _clean_candles(pd.read_csv(uploaded))
                source_label = uploaded.name
            except Exception as exc:
                st.error(f"Could not read candle CSV: {exc}")
                return

    elif source == "Permanent contract archive":
        item, candles = select_history("backtest_archive")
        if item is None:
            return
        selected_lot_size = int(item["lotsize"])
        source_label = f"{item['symbol']} · expiry {item['expiry']} · {item['interval']}"

    elif source == "Saved local history":
        data_dir = Settings.from_env().data_dir
        saved = sorted((data_dir / "live").rglob("*.parquet")) if (data_dir / "live").exists() else []
        if not saved:
            st.info("No saved live chart history yet. Connect a focused instrument first.")
            return
        selected_path = st.selectbox("Saved candle history", saved, format_func=lambda p: str(p.relative_to(data_dir)))
        try:
            candles = load_candles_parquet(selected_path)
            candles["timestamp"] = _local_timestamps(candles["timestamp"])
            source_label = str(selected_path.relative_to(data_dir))
            selected_lot_size = 1
            st.warning("Legacy token-only history has no verified historical contract metadata. "
                       "Verify units per lot below. Import it in Contract history with its original metadata "
                       "to preserve the expired contract identity.")
        except Exception as exc:
            st.error(f"Could not read saved candles: {exc}")
            return

    else:
        focus = st.selectbox("Market group", FOCUS_MARKETS, key="history_focus")
        try:
            catalogue = _catalogue(focus)
        except Exception as exc:
            st.error(f"Could not load Angel One's instrument master: {exc}")
            return
        if not catalogue:
            st.warning("No matching contracts found in the current Angel One instrument master.")
            return
        instrument = st.selectbox(
            "Instrument to download",
            catalogue,
            format_func=instrument_label,
            key="history_instrument",
        )
        selected_lot_size = int(instrument.get("lotsize") or 1)
        left, right = st.columns(2)
        from_day = left.date_input("From date", value=date.today() - timedelta(days=30))
        to_day = right.date_input("To date", value=date.today())
        interval = st.selectbox(
            "Candle interval",
            ["ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE", "ONE_HOUR", "ONE_DAY"],
            index=1,
            key="history_interval",
        )
        if from_day >= to_day:
            st.warning("The start date must be earlier than the end date.")
        if st.button("Download and cache historical candles", type="primary", disabled=from_day >= to_day):
            try:
                with st.spinner("Downloading candles from Angel One…"):
                    from app.broker.angel_auth import connect_market_data
                    from app.broker.angel_historical import get_candles
                    from app.broker.angel_live import AngelLiveFeed
                    session = connect_market_data()
                    candles = get_candles(
                        session.client,
                        exchange=str(instrument["exch_seg"]),
                        symbol_token=str(instrument["token"]),
                        interval=interval,
                        from_datetime=datetime.combine(from_day, time(9, 0)),
                        to_datetime=datetime.combine(to_day, time(23, 59)),
                    )
                    if candles.empty:
                        raise ValueError("Angel One returned no candles for this range.")
                    candles["timestamp"] = _local_timestamps(candles["timestamp"])
                    history_path = (
                        Settings.from_env().data_dir / "live" / str(instrument["exch_seg"])
                        / contract_id(instrument) / f"{interval}.parquet"
                    )
                    archive().save(instrument, interval, candles, source="angel-historical")
                    save_candles_parquet(candles, history_path)
                    st.session_state["last_history_download"] = str(history_path)
                st.success(f"Cached {len(candles):,} candles at {history_path}")
            except Exception as exc:
                st.error(f"Historical download failed: {exc}")
        last_path = st.session_state.get("last_history_download")
        if last_path and Path(last_path).exists():
            try:
                candles = load_candles_parquet(Path(last_path))
                candles["timestamp"] = _local_timestamps(candles["timestamp"])
                source_label = str(Path(last_path).name)
            except Exception as exc:
                st.error(f"Could not load downloaded candles: {exc}")
                return

    if candles is None:
        return

    st.caption(f"Data: {source_label or 'selected candles'} · {len(candles):,} rows")
    with st.form("backtest_settings"):
        a, b, c = st.columns(3)
        fast = a.number_input("Fast EMA", min_value=1, max_value=200, value=9)
        slow = b.number_input("Slow EMA", min_value=2, max_value=500, value=21)
        deposit_choice = c.selectbox(
            "Simulated deposit",
            ["₹10,000", "₹50,000", "₹1,00,000", "Custom"],
            index=2,
        )
        custom_deposit = c.number_input(
            "Custom deposit amount (₹)", min_value=1.0, value=100000.0, step=10000.0
        )
        d, e, f = st.columns(3)
        lots = d.number_input("Lots", min_value=1, max_value=1000, value=1, step=1)
        lot_size = e.number_input(
            "Units per lot", min_value=1, max_value=1000000,
            value=int(selected_lot_size), step=1,
            help="Loaded from Angel One's current instrument master where available. Editable for simulation."
        )
        brokerage = f.number_input(
            "Brokerage per executed order (₹)", min_value=0.0, value=20.0, step=1.0
        )
        g, h = st.columns(2)
        extra_per_lot = g.number_input(
            "Additional charge per lot per order (₹)", min_value=0.0, value=0.0, step=1.0
        )
        slippage = h.number_input("Slippage (basis points)", min_value=0.0, value=0.0, step=1.0)
        allow_short = st.checkbox("Allow short positions", value=False)
        run = st.form_submit_button("Run backtest", type="primary")

    if int(fast) >= int(slow):
        st.warning("Fast EMA must be shorter than Slow EMA.")
        return
    if not run:
        return

    deposits = {"₹10,000": 10000.0, "₹50,000": 50000.0, "₹1,00,000": 100000.0}
    deposit = float(custom_deposit) if deposit_choice == "Custom" else deposits[deposit_choice]
    strategy_frame = generate_ema_crossover_signals(
        candles, fast_period=int(fast), slow_period=int(slow), allow_short=allow_short
    )
    result = run_backtest(
        candles,
        strategy_frame["signal"],
        BacktestConfig(
            initial_capital=deposit,
            quantity=1.0,
            lots=int(lots),
            lot_size=int(lot_size),
            fee_per_order=float(brokerage),
            extra_charge_per_lot_order=float(extra_per_lot),
            slippage_bps=float(slippage),
            allow_short=allow_short,
        ),
    )
    metrics = result.metrics
    st.caption(
        f"Simulated deposit ₹{deposit:,.2f} · {int(lots)} lot(s) × "
        f"{int(lot_size)} units · ₹{float(brokerage):.2f} brokerage/order. "
        "This is a paper simulation balance; no funds are deposited."
    )
    top = st.columns(4)
    top[0].metric("Net P&L", f"₹{metrics['net_pnl']:,.2f}")
    top[1].metric("Final balance", f"₹{metrics['final_equity']:,.2f}")
    top[2].metric("Max drawdown", f"{metrics['max_drawdown_pct']:.2f}%")
    top[3].metric("Win rate", f"{metrics['win_rate_pct']:.2f}%")
    lower = st.columns(4)
    lower[0].metric("Trades", metrics["trades"])
    lower[1].metric("Total charges", f"₹{metrics['total_charges']:,.2f}")
    lower[2].metric("Profit factor", str(metrics["profit_factor"]))
    lower[3].metric("Max losing streak", metrics["max_consecutive_losses"])

    equity = result.equity_curve
    equity_fig = go.Figure(go.Scatter(
        x=equity["timestamp"], y=equity["equity"], mode="lines", name="Simulated balance"
    ))
    equity_fig.update_layout(title="Simulated balance history", height=340, template="plotly_white")
    st.plotly_chart(equity_fig, use_container_width=True)

    signals = generate_chart_signals(candles, fast_period=int(fast), slow_period=int(slow))
    st.plotly_chart(_chart(signals, title="EMA crossover signals"), use_container_width=True)
    st.subheader("Trades")
    trade_display = result.trades_frame()
    if not trade_display.empty:
        exposures = []
        for entry in pd.to_datetime(trade_display["entry_time"]):
            prior = candles[pd.to_datetime(candles["timestamp"]) < entry]
            raw = prior.iloc[-1].get("option_gamma") if not prior.empty else None
            exposures.append(gamma_exposure(
                float(raw) if raw is not None and pd.notna(raw) else None, int(lot_size), int(lots),
            ))
        trade_display["gamma_exposure"] = exposures
        trade_display["gamma_source"] = "CSV at signal candle" if "option_gamma" in candles else "unavailable"
    st.dataframe(style_gamma(trade_display), use_container_width=True, hide_index=True)
    st.download_button(
        "Download trades CSV",
        trade_display.to_csv(index=False).encode("utf-8"),
        file_name="mytrade_trades.csv",
        mime="text/csv",
    )

live_tab, backtest_tab, accuracy_tab, review_tab, history_view = st.tabs(
    ["Live chart and signals", "Backtesting", "Candle accuracy", "Prediction review", "Contract history"]
)
with live_tab:
    _live_tab()
with backtest_tab:
    _backtest_tab()
with accuracy_tab:
    render_accuracy_tab()
with review_tab:
    _review_tab()

with history_view:
    history_tab()
