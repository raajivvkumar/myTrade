"""Offline DhanHQ smoke and safety tests; no paid API calls."""
from argparse import Namespace
from datetime import date

import pytest

from app.broker.dhan_historical import DhanClient, RollingQuery, normalize, windows
from app.broker.dhan_cli import run_rolling


def query(**kwargs):
    params = dict(start=date(2026, 3, 23), end=date(2026, 3, 25))
    params.update(kwargs)
    return RollingQuery(**params)


def response():
    return {"data": {"pe": {
        "timestamp": [1774247100, 1774247160],
        "open": [5, 7], "high": [8, 9], "low": [4, 6],
        "close": [7, 8], "strike": [23000, 23050],
        "volume": [100, 150], "oi": [2000, 2200],
        "iv": [20, 21], "spot": [23010, 23040],
    }, "ce": None}}


def test_request_and_windows():
    q = query()
    assert q.payload()["securityId"] == 13
    assert q.payload()["expiryCode"] == 1
    assert q.payload()["toDate"] == "2026-03-25"
    parts = list(windows(date(2026, 1, 1), date(2026, 4, 1)))
    assert parts[0] == (date(2026, 1, 1), date(2026, 1, 31))
    assert parts[-1][1] == date(2026, 4, 1)
    assert all(parts[i][1] == parts[i+1][0] for i in range(len(parts)-1))


def test_validation_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        query(strike="ATM+11")
    with pytest.raises(ValueError):
        query(end=date(2026, 5, 1))
    with pytest.raises(ValueError):
        query(expiry_code=5)


def test_rolling_strike_is_not_fused_into_one_contract():
    f = normalize(response(), query())
    assert f.actual_strike.tolist() == [23000, 23050]
    assert f.relative_strike.tolist() == ["ATM", "ATM"]
    assert f.source_quality.eq("UNVALIDATED_ROLLING_NOT_FIXED_CONTRACT").all()
    assert f.timestamp.dt.tz is None


def test_malformed_data_fails_closed():
    bad = response()
    bad["data"]["pe"]["strike"] = [23000]
    with pytest.raises(ValueError, match="mismatched"):
        normalize(bad, query())
    bad = response()
    bad["data"]["pe"]["low"][0] = 10
    with pytest.raises(ValueError, match="OHLC"):
        normalize(bad, query())


def test_readonly_allowlist_and_no_token_leak():
    class R:
        status_code = 403
    class Session:
        def request(self, method, url, **kwargs):
            assert method == "GET"
            assert kwargs["headers"]["access-token"] == "secret"
            return R()
    c = DhanClient(token="secret", session=Session())
    with pytest.raises(RuntimeError) as error:
        c.profile()
    assert "secret" not in str(error.value)
    with pytest.raises(ValueError, match="allowlisted"):
        c._call("POST", "/orders", {})


def test_default_dry_run_needs_no_data_plan(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    args = Namespace(from_date=date(2026, 3, 23), to_date=date(2026, 3, 25),
                     expiry_flag="MONTH", expiry_code=1, strike="ATM",
                     side="PUT", interval=1, output_dir=str(tmp_path),
                     pause_seconds=1, execute=False)
    run_rolling(args)
    assert "DRY RUN" in capsys.readouterr().out
    assert not list(tmp_path.rglob("*"))


@pytest.mark.parametrize("code", [1, 2, 3])
def test_expired_rolling_uses_endpoint_specific_one_based_codes(code):
    assert query(expiry_code=code).payload()["expiryCode"] == code


@pytest.mark.parametrize("code", [0, True, -1, 4])
def test_invalid_expired_codes_are_rejected_before_broker_access(code, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid expiry code must fail before creating a broker client")
    monkeypatch.setattr("app.broker.dhan_cli.DhanClient", forbidden)
    args = Namespace(from_date=date(2026, 3, 23), to_date=date(2026, 3, 25),
                     expiry_flag="WEEK", expiry_code=code, strike="ATM",
                     side="CALL", interval=1, output_dir=None,
                     pause_seconds=1, execute=True)
    with pytest.raises(ValueError, match="expiry code"):
        run_rolling(args)


@pytest.mark.parametrize("code,strike", [(2, "ATM+4"), (3, "ATM-4")])
def test_next_far_offsets_outside_provider_range_fail_locally(code, strike):
    with pytest.raises(ValueError, match="offsets up to 3"):
        query(expiry_code=code, strike=strike)


def test_near_offsets_and_far_supported_offsets_remain_available():
    assert query(expiry_code=1, strike="ATM+10").payload()["strike"] == "ATM+10"
    assert query(expiry_code=3, strike="ATM-3").payload()["strike"] == "ATM-3"
