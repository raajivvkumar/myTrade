"""Offline/no-broker tests for Upstox Basic read-only historical availability."""
from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.broker import upstox_basic_history as history
from app.broker import upstox_basic_history_cli as cli

IST = ZoneInfo("Asia/Kolkata")
DAY = "2026-10-07"
INDEX = "NSE_INDEX|Nifty 50"


class FakeResponse:
    status_code = 200

    def __init__(self, data=None, status_code=200):
        self.data = data
        self.status_code = status_code

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def candle(minute="09:15", date_string=DAY, *, high=101, low=99, open_=100, close=100):
    return [
        f"{date_string}T{minute}:00+05:30",
        open_, high, low, close, 250, 500,
    ]


def test_v3_index_get_exact_route_and_no_credentials_leak(capsys):
    session = FakeSession(FakeResponse({"status": "success", "data": {"candles": [
        candle("09:16"), candle("09:15")
    ]}}))
    rows = history.fetch_day_1m(INDEX, DAY, token="secret-test-value", session=session)
    assert [row["timestamp"][-14:-6] for row in rows] == ["09:15:00", "09:16:00"]
    assert len(rows) == 2
    url, req = session.calls[0]
    assert url == (
        "https://api.upstox.com/v3/historical-candle/"
        "NSE_INDEX%7CNifty%2050/minutes/1/2026-10-07/2026-10-07"
    )
    assert req["headers"]["Authorization"] == "Bearer secret-test-value"
    assert "secret-test-value" not in capsys.readouterr().out


def test_missing_token_does_not_make_request(monkeypatch):
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    monkeypatch.setattr(history, "load_dotenv", lambda: None)
    session = FakeSession(FakeResponse({}))
    with pytest.raises(RuntimeError, match="UPSTOX_ANALYTICS_TOKEN"):
        history.fetch_day_1m(INDEX, DAY, session=session)
    assert session.calls == []


@pytest.mark.parametrize("instrument,day", [
    ("NSE_FO|", DAY),
    ("NSE_FO|ABC/../../orders", DAY),
    (INDEX, "2026-09-32"),
    (INDEX, "2026-10-07x"),
])
def test_bad_identity_or_date_rejected_without_request(instrument, day):
    session = FakeSession(FakeResponse({}))
    with pytest.raises(ValueError):
        history.fetch_day_1m(instrument, day, token="test", session=session)
    assert session.calls == []


@pytest.mark.parametrize("rows", [
    [candle("09:15"), candle("09:15")],
    [candle("09:15", date_string="2026-10-06")],
    [candle("09:15", high=98)],
    [candle("09:15", low=102)],
    [candle("09:15", open_=-1)],
    [["2026-10-07T09:15:00", 100, 101, 99, 100, 1, 2]],
    [["2026-10-07T09:15:30+05:30", 100, 101, 99, 100, 1, 2]],
])
def test_invalid_candles_are_not_promoted(rows):
    session = FakeSession(FakeResponse({"status": "success", "data": {"candles": rows}}))
    with pytest.raises(ValueError):
        history.fetch_day_1m(INDEX, DAY, token="test", session=session)


def test_http_error_has_no_body_token_and_no_generated_market_data():
    session = FakeSession(FakeResponse({"error": "secret"}, status_code=403))
    with pytest.raises(RuntimeError, match="HTTP 403") as error:
        history.fetch_day_1m(INDEX, DAY, token="never-print-me", session=session)
    assert "never-print-me" not in str(error.value)
    assert "secret" not in str(error.value)


def sample_valid_full_session():
    day = datetime.fromisoformat(f"{DAY}T09:15:00+05:30")
    return [{
        "timestamp": (day + timedelta(minutes=i)).isoformat(),
        "open": 100., "high": 101., "low": 99., "close": 100.,
        "volume": 0., "oi": 0., "instrument_key": INDEX
    } for i in range(375)]


def test_quality_requires_exactly_full_normal_session():
    full = sample_valid_full_session()
    assert history.candle_quality(full)["research_ready_contiguous_session"] is True
    gap = full[:50] + full[51:]
    r = history.candle_quality(gap)
    assert r["missing_regular_minutes"] == 1
    assert r["research_ready_contiguous_session"] is False
    outside = full + [{
        **full[-1], "timestamp": f"{DAY}T15:30:00+05:30",
    }]
    r2 = history.candle_quality(outside)
    assert r2["off_session_candles"] == 1
    assert r2["research_ready_contiguous_session"] is False


def fake_chain(actual_expiry=None):
    expiry = actual_expiry or (
        datetime.now(IST).date() + timedelta(days=7)
    ).isoformat()
    market = {
        "ltp": 40, "volume": 500, "oi": 2000, "prev_oi": 1800,
        "bid_price": 39.5, "ask_price": 40.5,
    }
    greeks = {"gamma": 0.002, "delta": -0.45, "iv": 20, "theta": -3}
    return {
        "status": "success",
        "data": [{
            "expiry": expiry, "strike_price": 22000,
            "underlying_key": INDEX, "underlying_spot_price": 22010,
            "put_options": {
                "instrument_key": "NSE_FO|EXACT123", "market_data": market,
                "option_greeks": greeks,
            }
        }]
    }


def test_option_lookup_uses_exact_strike_expiry_and_side(monkeypatch):
    seen = []
    monkeypatch.setattr(history, "get_option_chain", lambda **k: (
        seen.append(k["expiry"]), fake_chain()
    )[1])
    selected = history.resolve_active_option(
        "current_week", 22000, "PE", token="test"
    )
    assert seen == ["current_week"]
    assert selected["instrument_key"] == "NSE_FO|EXACT123"
    assert selected["strike_price"] == 22000
    assert selected["option_type"] == "PE"
    with pytest.raises(ValueError, match="not uniquely listed"):
        history.resolve_active_option("current_week", 22050, "PE", token="test")


def test_expired_option_rejected(monkeypatch):
    expired = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
    monkeypatch.setattr(history, "get_option_chain", lambda **kwargs: fake_chain(expired))
    with pytest.raises(ValueError, match="Expired"):
        history.resolve_active_option("current_week", 22000, "PE", token="test")


def arg(**kwargs):
    base = dict(mode="index", date=DAY, strike=None, side=None,
                expiry="current_week", execute=False)
    base.update(kwargs)
    return Namespace(**base)


def test_cli_dry_run_does_not_contact_broker(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "fetch_day_1m", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("unwanted API call")
    ))
    assert cli.run(arg())["requests"] == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_cli_option_probe_readonly_two_calls_no_archive(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = []
    def resolve(*args, **kwargs):
        calls.append("chain")
        return {
            "instrument_key": "NSE_FO|EXACT123",
            "strike_price": 22000.,
            "expiry": "2026-10-27", "option_type": "PE",
        }
    def fetch(*args, **kwargs):
        calls.append("candles")
        return []
    monkeypatch.setattr(cli, "resolve_active_option", resolve)
    monkeypatch.setattr(cli, "fetch_day_1m", fetch)
    result = cli.run(arg(
        mode="option", strike=22000., side="PE", date=DAY, execute=True
    ))
    assert result["status"] == "EMPTY"
    assert calls == ["chain", "candles"]
    assert result["requests"] == 2
    assert not list(tmp_path.iterdir())


def test_cli_future_date_rejected_before_option_api(monkeypatch):
    monkeypatch.setattr(
        cli, "resolve_active_option",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not call")),
    )
    future = (datetime.now(IST).date() + timedelta(days=2)).isoformat()
    with pytest.raises(ValueError, match="Future"):
        cli.run(arg(
            mode="option", date=future, strike=22000., side="PE", execute=True
        ))
