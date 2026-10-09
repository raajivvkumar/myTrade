"""Mocked data-access checks; no actual broker authorization or market validation."""
from datetime import date, datetime, timedelta

import pytest
import requests

from app.broker import dhan_cli
from app.broker.dhan_historical import DhanClient
from app.broker.dhan_probe import IST, nifty_payload, run_nifty_probe, summarize_nifty

DAY = date(2026, 10, 8)


def candles(count=15):
    start = datetime(2026, 10, 8, 9, 15, tzinfo=IST)
    return {"timestamp": [int((start + timedelta(minutes=i)).timestamp()) for i in range(count)],
            "open": [25000] * count, "high": [25010] * count,
            "low": [24990] * count, "close": [25005] * count}


class FakeClient:
    def __init__(self, plan="Active", data=None):
        self.calls = []
        self.plan = plan
        self.data = data if data is not None else candles()

    def profile(self):
        self.calls.append("profile")
        return {"dataPlan": self.plan, "dataValidity": "test-only"}

    def _call(self, method, path, payload):
        self.calls.append((method, path, payload))
        return self.data


def test_default_probe_never_constructs_client(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Dry run must not create a broker client")
    monkeypatch.setattr("app.broker.dhan_probe.DhanClient", forbidden)
    result = run_nifty_probe(DAY)
    assert result["network_requests"] == 0
    assert result["saved_files"] == 0
    assert result["request"] == nifty_payload(DAY)


@pytest.mark.parametrize("plan", ["Deactive", "Unknown", "", None])
def test_inactive_plan_does_not_fetch_candles(plan):
    client = FakeClient(plan=plan)
    with pytest.raises(RuntimeError, match="not active"):
        run_nifty_probe(DAY, execute=True, client=client)
    assert client.calls == ["profile"]


def test_probe_makes_only_profile_and_one_bounded_intraday_call():
    client = FakeClient()
    result = run_nifty_probe(DAY, execute=True, client=client)
    assert client.calls == ["profile", ("POST", "/charts/intraday", nifty_payload(DAY))]
    assert result["candle_count"] == 15
    assert result["sample_coverage"] == "COMPLETE"
    assert result["saved_files"] == 0
    assert result["first_timestamp_ist"] == "2026-10-08T09:15:00+05:30"
    assert result["independently_verified"] is False
    assert result["fixed_option_contract_verified"] is False


def test_gap_is_reported_without_fabricated_candle():
    data = candles()
    for values in data.values():
        del values[2]
    result = summarize_nifty(data, DAY)
    assert result["candle_count"] == 14
    assert result["sample_coverage"] == "INCOMPLETE"
    assert result["missing_minutes_ist"] == ["2026-10-08T09:17:00+05:30"]


@pytest.mark.parametrize("issue", ["empty", "length", "ohlc", "nan", "infinite",
                                  "duplicate", "reverse", "outside", "subminute"])
def test_bad_candle_data_rejected(issue):
    data = candles()
    if issue == "empty":
        data = {key: [] for key in data}
    elif issue == "length":
        data["high"].pop()
    elif issue == "ohlc":
        data["low"][0] = 26000
    elif issue == "nan":
        data["close"][0] = float("nan")
    elif issue == "infinite":
        data["high"][0] = float("inf")
    elif issue == "duplicate":
        data["timestamp"][1] = data["timestamp"][0]
    elif issue == "reverse":
        data["timestamp"].reverse()
    elif issue == "outside":
        data["timestamp"][0] -= 60
    else:
        data["timestamp"][0] += 1
    with pytest.raises(ValueError):
        summarize_nifty(data, DAY)


def test_actual_client_intraday_request_disables_redirects():
    class Response:
        status_code = 200
        def json(self):
            return candles()
    class Session:
        def request(self, method, url, **kwargs):
            assert (method, url) == ("POST", "https://api.dhan.co/v2/charts/intraday")
            assert kwargs["allow_redirects"] is False
            assert kwargs["timeout"] == 30
            assert kwargs["json"] == nifty_payload(DAY)
            return Response()
    client = DhanClient(token="synthetic", session=Session())
    assert client._call("POST", "/charts/intraday", nifty_payload(DAY)) == candles()
    with pytest.raises(ValueError, match="allowlisted"):
        client._call("POST", "/orders", {})


def test_checkout_env_loads_only_on_execution_and_preserves_process_env(monkeypatch, tmp_path):
    monkeypatch.setattr(dhan_cli, "__file__", str(tmp_path / "app" / "broker" / "dhan_cli.py"))
    (tmp_path / ".env").write_text("DHAN_ACCESS_TOKEN=synthetic-file-token\n", encoding="utf-8")
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    dhan_cli.load_local_credentials()
    import os
    assert os.environ["DHAN_ACCESS_TOKEN"] == "synthetic-file-token"
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "synthetic-process-token")
    dhan_cli.load_local_credentials()
    assert os.environ["DHAN_ACCESS_TOKEN"] == "synthetic-process-token"


def test_cli_preview_reads_no_credentials(monkeypatch, capsys):
    def forbidden():
        pytest.fail("Dry run should not read .env")
    monkeypatch.setattr(dhan_cli, "load_local_credentials", forbidden)
    monkeypatch.setattr("sys.argv", ["dhan_cli", "nifty", "--date", "2026-10-08"])
    dhan_cli.main()
    assert "DRY_RUN" in capsys.readouterr().out


def test_cli_network_failure_hides_exception_chain_and_secret(monkeypatch):
    class Session:
        def request(self, *args, **kwargs):
            raise requests.ConnectionError("synthetic-secret-in-network-exception")
    monkeypatch.setattr(dhan_cli, "load_local_credentials", lambda: None)
    monkeypatch.setattr(dhan_cli, "DhanClient", lambda: DhanClient(token="synthetic", session=Session()))
    monkeypatch.setattr("sys.argv", ["dhan_cli", "status"])
    with pytest.raises(SystemExit) as error:
        dhan_cli.main()
    assert "synthetic-secret" not in str(error.value)
    assert error.value.__suppress_context__
