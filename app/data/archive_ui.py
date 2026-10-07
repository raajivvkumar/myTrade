"""Offline history browsing, import, export and accuracy replay."""
import json
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from app.config import Settings
from app.data.archive import HistoryArchive, INTERVAL_MINUTES


def archive():
    return HistoryArchive(Settings.from_env().data_dir / "archive" / "history.sqlite3")


def label(item):
    return f"{item['symbol']} · {item['exch_seg']} · expiry {item['expiry'] or 'none'} · {item['interval']} · {item['candles']} candles"


def select_history(key):
    items = archive().catalogue()
    if not items:
        st.info("No archived candles yet. Download/connect a contract or import a historical CSV below.")
        return None, None
    item = st.selectbox("Archived contract (includes expired)", items, format_func=label, key=key)
    return item, archive().load(item["id"], item["interval"])


def history_tab():
    st.subheader("Permanent contract history")
    st.caption("Saved on this computer, independent of the broker catalogue. Expiry never deletes data. "
               "Only data actually downloaded/imported or collected while the app is running is saved. "
               "Keep a backup on another disk; this is not cloud storage.")
    item, frame = select_history("archive_browse")
    if item:
        st.write(f"Saved range: {item['first']} → {item['last']} · historical lot size {item['lotsize']}")
        st.dataframe(frame.tail(500), hide_index=True, use_container_width=True)
        st.download_button("Export archived candles CSV", frame.to_csv(index=False),
                           file_name=f"{item['id'][:12]}_{item['interval']}.csv", mime="text/csv")
        st.download_button("Export contract metadata JSON", json.dumps({k: item[k] for k in
            ("exch_seg", "token", "symbol", "name", "expiry", "strike", "lotsize", "instrumenttype")}, indent=2),
            file_name=f"{item['id'][:12]}_contract.json", mime="application/json")
        if st.button("Measure archived candle accuracy"):
            from app.review.candle_accuracy import historical_accuracy
            from app.review.accuracy_ui import show_accuracy
            try:
                show_accuracy(historical_accuracy(frame, minutes=INTERVAL_MINUTES[item["interval"]]))
            except ValueError as exc:
                st.error(str(exc))
    with st.expander("Import expired or previously downloaded contract"):
        st.caption("Upload OHLC CSV plus its original contract metadata JSON. Naive timestamps are interpreted as India time. "
                   "Metadata fields: exch_seg, token, symbol, name, expiry, strike (original provider units), lotsize, instrumenttype. "
                   "Optional per-candle OI/IV/Greeks columns are retained as supplied; missing values are not reconstructed.")
        candles = st.file_uploader("History CSV", type=["csv"], key="archive_csv")
        metadata = st.file_uploader("Original contract JSON", type=["json"], key="archive_meta")
        interval = st.selectbox("Archive interval", list(INTERVAL_MINUTES), index=2)
        source = st.text_input("Original data source", placeholder="e.g. Angel One historical CSV")
        if st.button("Save imported history"):
            if candles is None or metadata is None or not source.strip():
                st.error("Provide both files and the original source.")
            else:
                try:
                    archive().save(json.load(metadata), interval, pd.read_csv(candles), source="import: " + source.strip())
                    st.success("History saved. It remains available after expiry and app restart.")
                    st.rerun()
                except (ValueError, TypeError, KeyError) as exc:
                    st.error(str(exc))
    if st.button("Prepare complete history backup"):
        with tempfile.TemporaryDirectory() as folder:
            path = archive().backup(Path(folder) / "history.sqlite3")
            st.session_state["archive_backup"] = path.read_bytes()
    if "archive_backup" in st.session_state:
        st.download_button("Download history database backup", st.session_state["archive_backup"],
                           file_name="mytrade_history.sqlite3", mime="application/octet-stream")
