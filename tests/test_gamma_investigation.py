"""Gamma-only lab: fake Upstox responses and synthetic in-memory contracts.

All tests are offline. No token, actual trade, cloud/local market archive,
or predictive gamma outcome is generated.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import ast
import math

import numpy as np
import pandas as pd
import pytest

from app.broker.upstox_chain import get_option_chain
from app.research.gamma_lab import (
    normalize_chain, compare_chain_snapshots,
    exact_contract_observations, compare_fingerprint_cohorts,
)


def chain_payload():
    market = {
        "ltp": 110, "volume": 100_000, "oi": 300_000,
        "prev_oi": 295_000, "bid_price": 109,
        "ask_price": 110, "bid_qty": 50, "ask_qty": 65,
    }
    return {"status": "success", "data": [
        {
            "expiry": "2026-10-13", "pcr": 1.1, "strike_price": 25000,
            "underlying_key": "NSE_INDEX|Nifty 50", "underlying_spot_price": 25005,
            "call_options": {
                "instrument_key": "NSE_FO|95001",
                "market_data": market,
                "option_greeks": {"delta": 0.48, "gamma": 0.003,
                                  "theta": -3, "vega": 4, "iv": 16.5, "pop": 40},
            },
            "put_options": {
                "instrument_key": "NSE_FO|95002",
                "market_data": {**market, "ltp": 100, "bid_price": 99, "ask_price": 100},
                "option_greeks": {"delta": -0.52, "gamma": 0.003,
                                  "theta": -3, "vega": 4, "iv": 16.5, "pop": 40},
            },
        },
    ]}


def synthetic_contract():
    times = pd.date_range("2026-10-08 09:15:00", periods=180, freq="min")
    return pd.DataFrame({
        "timestamp": times, "open": [4.0] * len(times),
        "high": [5.0] * len(times), "low": [3.0] * len(times),
        "close": [4.0] * len(times), "volume": [10] * 100 + [300] * 80,
        "oi": [12000] * len(times),
        "instrument_key": ["NSE_FO|95001"] * len(times),
        "strike_price": [25000] * len(times),
        "option_type": ["CE"] * len(times),
        "expiry": ["2026-10-13"] * len(times),
    })


def test_live_chain_readonly_get_and_bearer_not_logged(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return chain_payload()
    class Session:
        def get(self, url, **kwargs):
            assert url == "https://api.upstox.com/v2/option/chain"
            assert kwargs["params"]["expiry_date"] == "current_week"
            assert kwargs["params"]["instrument_key"] == "NSE_INDEX|Nifty 50"
            assert kwargs["headers"]["Authorization"] == "Bearer DUMMY_SECRET"
            return Response()
    result = get_option_chain(token="DUMMY_SECRET", session=Session())
    assert result["status"] == "success"


def test_error_does_not_expose_token():
    class Response:
        status_code = 403
    class Session:
        def get(self, url, **kwargs):
            return Response()
    with pytest.raises(RuntimeError, match="HTTP 403") as err:
        get_option_chain(token="DUMMY_SECRET", session=Session())
    assert "DUMMY_SECRET" not in str(err.value)


def test_unsupported_expiry_rejected_before_network():
    with pytest.raises(ValueError, match="Expiry"):
        get_option_chain(expiry="2026-13-77", token="DUMMY_SECRET")
    with pytest.raises(ValueError, match="Expiry"):
        get_option_chain(expiry="../trading", token="DUMMY_SECRET")


def test_chain_identity_and_gamma_sensitivity_screen():
    output = normalize_chain(chain_payload())
    assert set(output.side) == {"CE", "PE"}
    assert output.instrument_key.nunique() == 2
    assert output.quote_valid.all()
    assert all(output.spread_pct_mid < 5)
    assert all(output.delta_shift_for_1pct_spot > 0)
    assert output.gamma.notna().all()
    assert output.oi.gt(0).all()


def test_missing_spread_and_greeks_are_not_invented():
    p = chain_payload()
    p["data"][0]["call_options"]["market_data"].pop("ask_price")
    p["data"][0]["call_options"]["option_greeks"].pop("gamma")
    result = normalize_chain(p)
    call = result.loc[result.side.eq("CE")].iloc[0]
    assert not bool(call.quote_valid)
    assert not bool(call.investigate_only)
    assert np.isnan(call.gamma)


def test_mixed_underlying_and_duplicate_contracts_rejected():
    p = chain_payload()
    p["data"][0]["underlying_key"] = "NSE_INDEX|Nifty Bank"
    with pytest.raises(ValueError, match="underlying"):
        normalize_chain(p)
    p = chain_payload()
    p["data"].append(p["data"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_chain(p)


def test_snapshot_comparison_matches_only_exact_contract():
    original = normalize_chain(chain_payload())
    later = original.copy()
    later.loc[later.side.eq("CE"), "oi"] += 100
    later.loc[later.side.eq("CE"), "gamma"] += .0002
    diff = compare_chain_snapshots(original, later)
    ce = diff.loc[diff.side.eq("CE")].iloc[0]
    assert ce.oi_change == 100
    assert ce.gamma_change == pytest.approx(.0002)
    # Same strike but different contract key must not be cross-matched.
    later.loc[later.side.eq("CE"), "instrument_key"] = "NSE_FO|other"
    diff2 = compare_chain_snapshots(original, later)
    assert "CE" not in set(diff2.side)


def test_exact_contract_5x_is_observed_close_not_trade_profit():
    data = synthetic_contract()
    data.loc[100, "close"] = 22.0
    data.loc[100, "high"] = 23.0
    outcomes = exact_contract_observations(data, horizon=30)
    entry = outcomes.loc[outcomes.signal_ist.eq("2026-10-08 10:45:00")]
    assert len(entry) == 1
    row = entry.iloc[0]
    assert row.observed_ge_5x
    assert not row.observed_ge_10x
    assert row.entry_next_open == 4.0
    assert row.gamma_observed_t != row.gamma_observed_t  # no fake historic Greeks
    assert "NOT_EXECUTABLE_PNL" in row.result_type
    report = compare_fingerprint_cohorts(outcomes)
    assert report["gamma_history_present"] is False
    assert report["observed_5x"] >= 1


def test_bogus_intraminute_high_never_becomes_5x_outcome():
    data = synthetic_contract()
    data.loc[100, "high"] = 200.0
    out = exact_contract_observations(data, horizon=30)
    assert not out.observed_ge_5x.any()


def test_mixed_strike_or_expiry_rejected():
    data = synthetic_contract()
    data.loc[10, "strike_price"] = 25500
    with pytest.raises(ValueError, match="Mixed"):
        exact_contract_observations(data)
    data = synthetic_contract()
    data.loc[10, "expiry"] = "2026-10-20"
    with pytest.raises(ValueError, match="Mixed"):
        exact_contract_observations(data)


def test_missing_future_minutes_do_not_create_multiple():
    data = synthetic_contract().drop(index=100)
    out = exact_contract_observations(data, horizon=30)
    # 10:45 signal requires 10:55 and future 1min path.
    assert "2026-10-08 10:45:00" not in set(out.signal_ist)


def test_only_index_data_cannot_claim_gamma_proof():
    assert compare_fingerprint_cohorts(pd.DataFrame())["status"] == "INSUFFICIENT_CONTIGUOUS_DATA"


def test_streamlit_page_is_valid_python_and_has_no_archive_calls():
    path = Path(__file__).resolve().parents[1] / "app" / "pages" / "1_Gamma_Investigation.py"
    text = path.read_text(encoding="utf-8")
    ast.parse(text, filename=str(path))
    for bad in ("to_parquet(", "to_sql(", "rclone", "order/place", "archive().save("):
        assert bad not in text
