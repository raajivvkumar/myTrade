"""MyTrade Gamma Investigation — no local market-data archive, no broker orders.

Run: python -m streamlit run app/dashboard.py
Open the 'Gamma Investigation' page in Streamlit sidebar.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app.broker.upstox_chain import get_option_chain
from app.research.gamma_lab import (
    compare_chain_snapshots,
    compare_fingerprint_cohorts,
    exact_contract_observations,
    normalize_chain,
)

st.set_page_config(page_title="MyTrade — Gamma Investigation", page_icon="🔎", layout="wide")
st.title("Gamma Investigation Lab")
st.caption(
    "NIFTY options · Upstox Basic Analytics Token · in-memory only · "
    "read-only, no trading or automatic archive"
)
st.warning(
    "Higher Gamma sensitivity is NOT a prediction of 3×/5×/10× premium. "
    "Spreads, Theta, IV, Delta, liquidity and contract expiry can dominate outcomes."
)

live_tab, evidence_tab = st.tabs(
    ["Live NIFTY option-chain investigation", "Historical exact-contract fingerprint experiment"]
)

with live_tab:
    st.subheader("Live Gamma and liquidity examination")
    expiry = st.selectbox(
        "NIFTY contract expiry",
        ("current_week", "next_week", "far_week",
         "current_month", "next_month", "far_month"),
        help="Official Upstox relative expiry. Each refresh makes ONE read-only GET request.",
    )
    st.caption(
        "Investigate rank is a within-expiry/side relative Gamma-sensitivity "
        "screen with basic liquidity checks. It is not a BUY/SELL signal."
    )
    if st.button("Fetch current option chain", type="primary"):
        try:
            with st.spinner("Reading Upstox option chain (GET only)…"):
                new = normalize_chain(get_option_chain(expiry))
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            prior = st.session_state.get("gamma_live_now")
            if (prior and prior["expiry_selection"] == expiry
                    and prior["retrieved_at"].date() == now.date()):
                st.session_state["gamma_live_previous"] = prior
            else:
                st.session_state.pop("gamma_live_previous", None)
            st.session_state["gamma_live_now"] = {
                "expiry_selection": expiry,
                "retrieved_at": now,
                "frame": new,
            }
            st.success(
                f"Received {len(new)} NIFTY option contracts. "
                "Only this browser session keeps the current and prior snapshots."
            )
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))

    current = st.session_state.get("gamma_live_now")
    if current is None:
        st.info(
            "Generate a fresh read-only Analytics Token in Upstox Developer Apps, "
            "then set UPSTOX_ANALYTICS_TOKEN in your PRIVATE local .env. "
            "Click Fetch to start; the app does not save broker data."
        )
    elif current["expiry_selection"] != expiry:
        st.info("Expiry selection changed. Click Fetch for the newly selected expiry.")
    else:
        chain = current["frame"]
        st.caption(f"Request completed at {current['retrieved_at'].strftime('%Y-%m-%d %H:%M:%S IST')} — underlying quotes may be delayed or stale, particularly after market close.")
        if chain.empty:
            st.warning("Upstox returned no listed option contracts for this expiry.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Contracts observed", len(chain))
            c2.metric("Investigate-only candidates", int(chain.investigate_only.sum()))
            c3.metric("Invalid/missing bid–ask quotes", int((~chain.quote_valid).sum()))
            c4.metric("Expiries returned", int(chain.expiry.nunique()))
            near = st.slider(
                "Show strikes within ±% of spot",
                min_value=1, max_value=15, value=5, step=1,
            )
            sides = st.multiselect("Side", ("CE", "PE"), default=("CE", "PE"))
            subset = chain.loc[
                chain.side.isin(sides) & chain.moneyness_pct.abs().le(near)
            ].copy()
            if subset.empty:
                st.warning("No contracts match the current filter.")
            else:
                cols = [
                    "expiry", "strike_price", "side", "spot", "ltp",
                    "gamma", "delta", "iv", "theta", "oi", "volume",
                    "bid", "ask", "spread_pct_mid", "delta_shift_for_1pct_spot",
                    "investigate_only", "instrument_key",
                ]
                st.dataframe(subset[cols].head(200), use_container_width=True,
                             hide_index=True)
                st.caption(
                    "delta_shift_for_1pct_spot = |Gamma| × spot × 0.01: "
                    "a small-move linear approximation, not expected profit. "
                    "Quote validity and relative gamma rank alone are not trade entries."
                )

        old = st.session_state.get("gamma_live_previous")
        if old and not old["frame"].empty and not chain.empty:
            st.subheader("Changes since previous refresh — same day and same exact contract")
            diff = compare_chain_snapshots(old["frame"], chain)
            if diff.empty:
                st.info("No exact instrument keys overlap with the prior snapshot.")
            else:
                show = [
                    "expiry", "strike_price", "side", "ltp_now", "ltp_change",
                    "oi_change", "volume_change", "iv_change", "gamma_change",
                    "delta_change", "spread_pct_mid_now",
                ]
                st.dataframe(diff[show].head(200), use_container_width=True,
                             hide_index=True)
                st.caption(
                    "Volume/OI changes are changes between TWO sampled snapshots; "
                    "they are not 1-minute data, not gamma multiplication evidence, "
                    "and cannot establish event timing."
                )
        if st.button("Clear in-memory snapshots"):
            st.session_state.pop("gamma_live_now", None)
            st.session_state.pop("gamma_live_previous", None)
            st.rerun()

with evidence_tab:
    st.subheader("Investigate observed 3×, 5× and 10× option price moves")
    st.caption(
        "Optional CSV is processed only in memory. No Parquet, SQLite, "
        "Google Drive or broker upload is performed."
    )
    st.markdown(
        "Provide **one genuine fixed-strike NIFTY option contract** with "
        "1-minute bars, columns "
        "`timestamp,open,high,low,close,volume,oi,instrument_key,"
        "strike_price,option_type,expiry`. "
        "Optional historical `gamma,delta,iv,theta` columns must reflect "
        "what was actually known at each timestamp."
    )
    uploaded = st.file_uploader(
        "Exact-contract 1-minute CSV (not a rolling ATM series)",
        type=["csv"], key="gamma_exact_contract_upload",
    )
    c1, c2 = st.columns(2)
    horizon = c1.slider("Look-forward horizon in minutes", 5, 120, 30, 5)
    min_premium = c2.number_input(
        "Minimum next-minute entry premium (₹)", min_value=0.05,
        value=2.0, step=0.5,
    )
    if st.button("Investigate fingerprints in memory", disabled=uploaded is None):
        try:
            frame = pd.read_csv(uploaded)
            with st.spinner("Checking exact contract, timestamps, outcomes and controls…"):
                observations = exact_contract_observations(
                    frame, horizon=horizon, min_premium=float(min_premium)
                )
                report = compare_fingerprint_cohorts(observations)
            st.session_state["gamma_in_memory_summary"] = report
            st.session_state["gamma_in_memory_observations"] = observations
        except (ValueError, TypeError, KeyError) as exc:
            st.error(f"Unsafe or incomplete option dataset: {exc}")
            st.session_state.pop("gamma_in_memory_summary", None)
            st.session_state.pop("gamma_in_memory_observations", None)

    summary = st.session_state.get("gamma_in_memory_summary")
    if summary:
        if not summary["eligible_windows"]:
            st.warning("No continuous 60-minute lookback + complete forward windows.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Eligible starting minutes", summary["eligible_windows"])
            c2.metric("Volume >2× watch windows", summary["volume_2x_windows"])
            c3.metric("Observed ≥5× windows", summary["observed_5x"])
            c4.metric("Observed ≥10× windows", summary["observed_10x"])
            st.caption(
                "These windows overlap and are not independent samples. "
                "Prices are retrospective maximum future minute closes, NOT "
                "executed orders or verified Gamma causation."
            )
            comparison = pd.DataFrame([
                {
                    "Observed maximum premium": f"≥{n}×",
                    "Volume >2× cohort rate": summary[f"flagged_{n}x_rate"],
                    "Other eligible cohort rate": summary[f"unflagged_{n}x_rate"],
                }
                for n in (3, 5, 10)
            ])
            st.dataframe(comparison, hide_index=True, use_container_width=True)
            if not summary["gamma_history_present"]:
                st.warning(
                    "No historical Greek data: Gamma itself cannot be studied "
                    "or credited as the cause of any premium moves."
                )
            rows = st.session_state["gamma_in_memory_observations"]
            st.dataframe(
                rows.loc[
                    rows.observed_ge_3x | rows.watch_volume_2x
                ].head(100),
                hide_index=True, use_container_width=True,
            )
            st.download_button(
                "Download investigation CSV (manual only)",
                data=rows.to_csv(index=False).encode("utf-8"),
                file_name="mytrade_gamma_investigation_observations.csv",
                mime="text/csv",
            )
    st.info(
        "A real fingerprint needs many verified independent expiries, "
        "matched non-events, no look-ahead, liquidity-aware fills and "
        "out-of-sample holdouts. This first lab is descriptive, not calibrated."
    )
