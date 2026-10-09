"""All fixtures synthetic; no real Dhan credentials or broker orders."""
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd

from app.research.dhan_direct_ram_study import (
    queries, study_chunk, aggregate, merge_day, run,
)


def sample(peak=None, strike_change=None, missing_oi=False):
    stamps = pd.date_range("2026-10-06 09:15", periods=260, freq="min")
    df = pd.DataFrame({
        "timestamp": stamps, "actual_strike": 25000.,
        "open": 10., "high": 11., "low": 9., "close": 10.,
        "volume": 10., "oi": 1000., "iv": .2, "spot": 25000.,
    })
    if peak is not None:
        df.loc[155, "close"] = peak
        df.loc[155, "high"] = max(peak, 11.)
    if strike_change is not None:
        df.loc[strike_change:, "actual_strike"] = 25050.
    if missing_oi:
        df["oi"] = np.nan
    return df


def test_full_request_universe_covers_both_sides_and_all_offsets():
    requests = list(queries(date(2026, 10, 6), date(2026, 10, 6), full=True))
    assert len(requests) == 140
    assert len({(r.expiry_flag, r.expiry_code, r.strike, r.side)
                for r in requests}) == 140
    assert len(list(queries(date(2026, 10, 6), date(2026, 10, 6)))) == 2


def test_proxy_10x_is_retrospective_not_certified():
    r = study_chunk(sample(peak=200.))
    d = r["2026-10-06"]
    assert d["labeled"] > 0
    assert d["positives"]["10"] > 0
    assert d["rules"]["momentum20"]["scores"]["10"]["fn"] > 0


def test_future_switch_censored_without_false_negative():
    control = study_chunk(sample())["2026-10-06"]
    switched = study_chunk(sample(strike_change=146))["2026-10-06"]
    assert switched["switches"] == 1
    assert switched["censored_future"] > 0
    assert switched["labeled"] < control["labeled"]


def test_missing_oi_does_not_infer_positive_rule():
    d = study_chunk(sample(missing_oi=True))["2026-10-06"]
    assert d["rules"]["volume2_oi10"]["missing"] == d["labeled"]
    assert d["rules"]["volume2_oi10"]["scores"]["2"]["tp"] == 0


def test_lookback_discontinuity_rejected():
    f = sample()
    f = f.drop(index=90).reset_index(drop=True)
    d = study_chunk(f)["2026-10-06"]
    assert d["excluded_past"] > 0


def test_holdout_is_chronological_by_date():
    result = {}
    fixture = study_chunk(sample())["2026-10-06"]
    for i in range(12):
        merge_day(result, {f"2026-09-{i+1:02d}": fixture})
    c = aggregate(result)["chronological_session_holdout"]
    assert c["earlier_last"] < c["later_first"]
    assert c["later"]["dates"] >= 2


def args(execute=False, max_requests=None):
    return SimpleNamespace(
        from_date=date(2026, 10, 6), through=date(2026, 10, 6),
        full=False, horizon=30, min_price=2.0, pause=.25,
        max_requests=max_requests, progress=False, execute=execute,
    )


def test_default_preview_has_zero_network_and_zero_disk(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = run(args(), sleeper=lambda _: None)
    assert r["status"] == "PREVIEW_NO_API_CALLS"
    assert r["completed_api_requests"] == 0
    assert r["market_files_saved"] == 0
    assert r["real_2x_3x_5x_10x_multiplier_events"] is None
    assert not list(tmp_path.iterdir())


class MockClient:
    def __init__(self, empty=False):
        self.calls = []
        self.empty = empty

    def profile(self):
        return {"dataPlan": "Active"}

    def _call(self, method, path, payload):
        self.calls.append((method, path, payload))
        if self.empty:
            return {"data": {"ce": None}}
        f = sample()
        stamps = f.timestamp.dt.tz_localize("Asia/Kolkata")
        return {"data": {"ce": {
            "timestamp": (stamps.astype("int64") // 1_000_000_000).tolist(),
            "open": f.open.tolist(), "high": f.high.tolist(),
            "low": f.low.tolist(), "close": f.close.tolist(),
            "strike": f.actual_strike.tolist(),
            "volume": f.volume.tolist(), "oi": f.oi.tolist(),
            "iv": f.iv.tolist(), "spot": f.spot.tolist(),
        }}}


def test_authenticated_ram_only_one_response_no_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = MockClient()
    report = run(args(True, 1), client=client, sleeper=lambda _: None)
    assert report["status"] == "PARTIAL_MAX_REQUESTS"
    assert report["completed_api_requests"] == 1
    assert report["rows_observed"] == 260
    assert report["market_files_saved"] == 0
    assert report["historical_gamma_verified"] is False
    assert report["real_trading_accuracy"] is None
    assert client.calls[0][:2] == ("POST", "/charts/rollingoption")
    assert not list(tmp_path.iterdir())


def test_empty_dhan_response_is_audited_without_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = MockClient(empty=True)
    report = run(args(True, 1), client=client, sleeper=lambda _: None)
    assert report["completed_api_requests"] == 1
    assert report["empty_responses"] == 1
    assert report["rows_observed"] == 0
    assert not list(tmp_path.iterdir())
