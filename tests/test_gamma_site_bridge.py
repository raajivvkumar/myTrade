"""Protect the original Candle Lab UI contract from unsafe gamma outputs."""
from datetime import datetime, timezone
import json

import pytest
import pandas as pd

from app.research.candle_lab_bridge import (
    build_live_gamma_panel, build_event_study_panel,
)


def raw(*, key="NSE_FO|123", gamma=0.003, ask=100.0):
    market = {
        "ltp": 99.5, "volume": 12000, "oi": 24000,
        "prev_oi": 23500, "bid_price": 99.0, "ask_price": ask,
        "bid_qty": 25, "ask_qty": 25,
    }
    return {"status": "success", "data": [{
        "expiry": "2026-10-13", "strike_price": 25000,
        "underlying_spot_price": 25020, "underlying_key": "NSE_INDEX|Nifty 50",
        "pcr": 1.0,
        "call_options": {
            "instrument_key": key, "market_data": market,
            "option_greeks": {
                "gamma": gamma, "delta": .4, "iv": 19.5,
                "theta": -3, "vega": 2,
            }
        }
    }]}


NOW = datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc)


def test_does_not_change_original_ui_or_make_a_trade_signal():
    view = build_live_gamma_panel(raw(), retrieved_at=NOW)
    assert view["project"] == "myTrade Candle Lab"
    assert view["panel"] == "GAMMA_INVESTIGATION"
    assert view["trade_signal"] is None
    assert view["multiplier_probability"] is None
    assert view["order_allowed"] is False
    assert view["source"]["exchange_quote_timestamp_verified"] is False
    assert view["source"]["quote_freshness"] == "UNKNOWN"


def test_only_fixed_contract_and_exact_key_diff():
    old = raw()
    now = raw(gamma=.004)
    out = build_live_gamma_panel(now, retrieved_at=NOW, previous_chain=old)
    assert len(out["changes"]) == 1
    assert out["changes"][0]["id"]["instrument_key"] == "NSE_FO|123"
    assert out["changes"][0]["gamma_change"] == pytest.approx(.001)
    changed_key = raw(key="NSE_FO|DIFFERENT")
    other = build_live_gamma_panel(changed_key, retrieved_at=NOW, previous_chain=old)
    assert other["summary"]["exact_contract_changes"] == 0


def test_unknown_quotes_missing_gamma_become_json_null():
    data = raw(gamma=None, ask=None)
    out = build_live_gamma_panel(data, retrieved_at=NOW)
    assert out["summary"]["valid_bid_ask_quotes"] == 0
    assert out["summary"]["missing_gamma"] == 1
    assert not out["contracts"][0]["investigation_candidate"]
    assert "GREEKS_MISSING" in out["contracts"][0]["warnings"]
    dumped = json.dumps(out, allow_nan=False)
    assert '"gamma": null' in dumped


def test_raw_source_token_not_leaked_and_no_local_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = raw()
    data["token"] = "SECRET_TOKEN_TEST"
    data["data"][0]["call_options"]["access_token"] = "SECRET_TOKEN_TEST"
    output = build_live_gamma_panel(data, retrieved_at=NOW)
    assert "SECRET_TOKEN_TEST" not in json.dumps(output, allow_nan=False)
    assert not list(tmp_path.iterdir())


def test_naive_retrieval_time_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        build_live_gamma_panel(raw(), retrieved_at=datetime(2026, 10, 8, 15, 0))


def test_unverified_sample_counts_never_become_real_evidence():
    fake = {
        "observed_5x_events": 500,
        "observed_3x_events": 700,
        "independent_source_verified": False,
    }
    result = build_event_study_panel(fake, source_provenance="UNVERIFIED")
    assert result["status"] == "DATA_VERIFICATION_REQUIRED"
    assert result["counts"]["observed_5x_events"] is None
    assert result["validated_multiplier_probability"] is None
    assert result["trade_signal"] is None


def test_daily_nse_bhavcopy_only_is_not_minute_verified():
    result = build_event_study_panel(
        {"independent_source_verified": True, "observed_3x_events": 15},
        source_provenance="INDEPENDENT_DAILY_ONLY",
    )
    assert not result["minute_option_history_verified"]
    assert result["counts"]["observed_3x_events"] is None


def test_no_false_prediction_with_independent_minute_history():
    out = build_event_study_panel(
        {"independent_source_verified": True, "observed_3x_events": 2,
         "observed_5x_events": 1, "matched_controls": 2},
        source_provenance="INDEPENDENT_MINUTE_VERIFIED",
    )
    assert out["status"] == "DESCRIPTIVE_RESEARCH"
    assert out["counts"]["observed_5x_events"] == 1
    assert out["validated_multiplier_probability"] is None
    assert out["order_allowed"] is False


def test_invalid_provenance_rejected():
    with pytest.raises(ValueError):
        build_event_study_panel({}, source_provenance="GUESS")
