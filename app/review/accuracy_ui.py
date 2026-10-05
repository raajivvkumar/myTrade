"""Streamlit candle-accuracy view and consistent gamma highlighting."""
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.config import Settings
from app.review.candle_accuracy import CandleJournal, accuracy_summary, historical_accuracy


def journal():
    return CandleJournal(Settings.from_env().data_dir / "mytrade_journal.sqlite3")


def gamma_threshold():
    return st.session_state.get("gamma_threshold", 0.01)


def style_gamma(frame):
    def color(row):
        value = row.get("gamma_exposure")
        high = pd.notna(value) and float(value) >= gamma_threshold()
        return ["background-color: #fff1b8; color: #664500" if high else "" for _ in row]
    return frame.style.apply(color, axis=1)


def show_accuracy(records):
    summary = accuracy_summary(records)
    st.caption(
        "Experimental next-candle OHLC model. Direction match and price error are separate. "
        "Gold rows indicate gamma × lot size × lots above your threshold. Missing gamma is unknown."
    )
    if not summary["scored"]:
        st.info("No completed forecast targets to score yet.")
    else:
        cols = st.columns(4)
        cols[0].metric("Scored candles", summary["scored"])
        cols[1].metric("Close MAE", f'{summary["close_mae"]:.4f}')
        cols[2].metric("Close RMSE", f'{summary["close_rmse"]:.4f}')
        cols[3].metric("Close MAPE", f'{summary["close_mape_pct"]:.3f}%')
        cols = st.columns(3)
        cols[0].metric("Direction match", f'{summary["direction_accuracy_pct"]:.1f}%')
        cols[1].metric("Within saved tolerance", f'{summary["within_tolerance_pct"]:.1f}%')
        cols[2].metric("Previous-close baseline MAE", f'{summary["baseline_mae"]:.4f}')
        st.write("Model beats baseline on MAE." if summary["beats_baseline"] else
                 "Model has not beaten the previous-close baseline on MAE.")
        scored = records[records["status"] == "SCORED"].sort_values("actual_timestamp")
        fig = go.Figure()
        for name, column in [("Predicted close", "predicted_close"), ("Actual close", "actual_close")]:
            fig.add_trace(go.Scatter(x=scored["actual_timestamp"], y=scored[column], name=name))
        st.plotly_chart(fig, use_container_width=True)
    if not records.empty:
        st.dataframe(style_gamma(records), use_container_width=True, hide_index=True)
        st.download_button("Download candle accuracy CSV", records.to_csv(index=False).encode("utf-8"),
                           file_name="mytrade_candle_accuracy.csv", mime="text/csv")


def accuracy_tab():
    st.subheader("Predicted candle accuracy")
    source = st.radio("Forecast evidence", ["Saved live forecasts", "Historical OHLC replay"])
    if source == "Saved live forecasts":
        records = journal().list_records()
        if records.empty:
            st.info("Connect a live chart to save forecasts before their target candles complete.")
            return
        instrument = st.selectbox("Forecast instrument", records["instrument"].unique())
        records = records[records["instrument"] == instrument]
        interval = st.selectbox("Forecast interval", records["interval"].unique())
        records = records[records["interval"] == interval]
        st.caption("Latest 1,000 stored records. Forecast values are frozen on first write.")
        show_accuracy(records)
    else:
        st.caption(
            "Replay uses only earlier candles. Optional CSV columns: option_gamma, lot_size, lots. "
            "Supply Greeks captured at each row; never apply today's gamma to historical trades."
        )
        uploaded = st.file_uploader("Historical OHLC CSV for accuracy", type=["csv"], key="accuracy_csv")
        with st.form("accuracy_settings"):
            minutes = st.number_input("Interval in minutes", min_value=1, value=5)
            window = st.number_input("Forecast lookback", min_value=2, max_value=200, value=10)
            tolerance = st.number_input("Close-price tolerance (%)", min_value=0.0, value=0.1)
            run = st.form_submit_button("Measure candle accuracy")
        if run and uploaded is not None:
            try:
                records = historical_accuracy(pd.read_csv(uploaded), minutes=int(minutes),
                                              window=int(window), tolerance_pct=float(tolerance))
                show_accuracy(records)
            except ValueError as exc:
                st.error(str(exc))
