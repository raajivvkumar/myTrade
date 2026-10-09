"""Offline five-year plan, integrity, coverage and research-claim checks."""
from datetime import date, datetime

import pandas as pd
import pytest

from app.broker import dhan_history as history


def plan():
    return history.make_plan(date(2026, 10, 8), date(2026, 10, 9), ["ATM"], "WEEK", 0)


def response(rolling=False):
    stamps = [int(pd.Timestamp("2026-10-08 09:16", tz="Asia/Kolkata").timestamp()),
              int(pd.Timestamp("2026-10-08 09:17", tz="Asia/Kolkata").timestamp())]
    source = dict(timestamp=stamps, open=[100, 101], high=[102, 103],
                  low=[99, 100], close=[101, 102], volume=[20, 30])
    if rolling:
        source.update(strike=[22500, 22550], iv=[20, 21], oi=[1000, 1200], spot=[22510, 22560])
        return {"data": {"ce": source, "pe": None}}
    return source


def test_five_year_plan_latest_first_no_overlap():
    beginning, through = date(2021, 10, 9), date(2026, 10, 9)
    entries = history.make_plan(beginning, through, ["ATM"], "WEEK", 0)
    assert len(entries) == 183
    windows = entries[::3]
    assert windows[0]["end"] == "2026-10-10"
    assert windows[-1]["start"] == "2021-10-09"
    assert all(date.fromisoformat(x["end"])-date.fromisoformat(x["start"]) <= pd.Timedelta(days=30)
               for x in windows)
    assert all(windows[i]["start"] == windows[i+1]["end"] for i in range(len(windows)-1))
    assert entries[0]["series"] == "INDEX"
    assert entries[1]["payload"]["drvOptionType"] == "CALL"
    assert entries[2]["payload"]["drvOptionType"] == "PUT"


def test_preview_has_no_credentials_network_or_writes(monkeypatch, tmp_path, capsys):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 9, 12, tzinfo=tz)
    monkeypatch.setattr(history, "datetime", Clock)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["history", "--through", "2026-10-09"])
    def forbidden(*args, **kwargs):
        pytest.fail("Preview must not create a broker client or read .env")
    monkeypatch.setattr(history, "DhanClient", forbidden)
    monkeypatch.setattr(history, "load_local_credentials", forbidden)
    history.main()
    assert '"network_requests": 0' in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("strikes", [[], ["ATM", "ATM"], ["ATM+11"], ["BAD"]])
def test_bad_plan_rejected(strikes):
    with pytest.raises(ValueError):
        history.make_plan(date(2026, 10, 8), date(2026, 10, 9), strikes, "WEEK", 0)


def test_midnight_boundaries_and_leap_year():
    payload = plan()[0]["payload"]
    assert payload["fromDate"] == "2026-10-08 00:00:00"
    assert payload["toDate"] == "2026-10-10 00:00:00"
    assert history.five_year_start(date(2024, 2, 29)) == date(2019, 2, 28)


def test_rolling_strikes_kept_without_event_claims():
    frame = history.parse_bars(response(True), plan()[1])
    assert frame.actual_strike.tolist() == [22500, 22550]
    facts = history.daily_facts(frame)
    assert facts[0]["strike_switches_within_day"] == 1
    assert "observed_session_change_pct" not in facts[0]


@pytest.mark.parametrize("issue", ["duplicate", "reverse", "invalid_ohlc", "infinite", "length", "outside"])
def test_malformed_data_fail_closed(issue):
    raw = response()
    if issue == "duplicate":
        raw["timestamp"][1] = raw["timestamp"][0]
    elif issue == "reverse":
        raw["timestamp"].reverse()
    elif issue == "invalid_ohlc":
        raw["low"][0] = 200
    elif issue == "infinite":
        raw["high"][0] = float("inf")
    elif issue == "length":
        raw["volume"].pop()
    else:
        raw["timestamp"][0] -= 86400
    with pytest.raises(ValueError):
        history.parse_bars(raw, plan()[0])


def test_timestamp_convention_is_unknown_not_auto_fixed():
    frame = history.parse_bars(response(), plan()[0])
    facts = history.daily_facts(frame)[0]
    assert facts["timestamp_convention"] == "UNVERIFIED"
    assert facts["open_label_missing"] == 373
    assert facts["close_label_missing"] == 373
    assert len(frame) == 2


class Client:
    def __init__(self, active=True, fail=False):
        self.active, self.fail, self.calls = active, fail, []
    def profile(self):
        self.calls.append("profile")
        return {"dataPlan": "Active" if self.active else "Deactive"}
    def _call(self, method, endpoint, payload):
        self.calls.append((method, endpoint))
        if self.fail:
            raise RuntimeError("synthetic-secret-error")
        return response() if endpoint == "/charts/intraday" else {"data": {"ce": None, "pe": None}}


def test_inactive_plan_writes_nothing(tmp_path):
    client = Client(active=False)
    with pytest.raises(RuntimeError, match="not active"):
        history.collect(plan(), tmp_path, client=client)
    assert client.calls == ["profile"]
    assert not list(tmp_path.iterdir())


def test_resume_skips_existing_chunks_checksums_and_rebuilds_report(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    client = Client()
    report = history.collect(plan(), tmp_path, client=client)
    assert len(client.calls) == 4
    assert report["completed_chunks"] == 3
    assert report["empty_chunks"] == 2
    assert report["rows"] == 2
    assert report["collection_status"] == "FINISHED_WITH_EMPTY_WINDOWS"
    assert report["complete_five_year_market_coverage_confirmed"] is False
    assert report["fixed_contract_multiplier_event_count"] is None
    second = Client()
    history.collect(plan(), tmp_path, client=second)
    assert second.calls == ["profile"]
    manifest = history.load_cached(tmp_path, plan()[0])
    assert manifest["rows"] == 2
    parquet = history.chunk_base(tmp_path, plan()[0]).with_suffix(".parquet")
    parquet.write_bytes(b"damaged synthetic cache")
    with pytest.raises(ValueError, match="integrity"):
        history.load_cached(tmp_path, plan()[0])


def test_partial_run_reports_incomplete_and_can_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    report = history.collect(plan(), tmp_path, client=Client(), max_requests=1)
    assert report["collection_status"] == "PARTIAL"
    assert report["completed_chunks"] == 1
    next_client = Client()
    final = history.collect(plan(), tmp_path, client=next_client)
    assert len(next_client.calls) == 3
    assert final["completed_chunks"] == 3


def test_retry_empty_recollects_empty_only(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    history.collect(plan(), tmp_path, client=Client())
    retry = Client()
    history.collect(plan(), tmp_path, client=retry, retry_empty=True)
    assert len(retry.calls) == 3


def test_failure_keeps_report_sanitized(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError):
        history.collect(plan(), tmp_path, client=Client(fail=True))
    output = (tmp_path / "history_summary.json").read_text()
    assert "synthetic-secret" not in output
    assert "REQUEST_OR_VALIDATION_FAILED" in output
    assert '"collection_status": "PARTIAL"' in output

def test_intraday_open_interest_alias_is_preserved():
    raw = response()
    raw["open_interest"] = [1000, 1200]
    frame = history.parse_bars(raw, plan()[0])
    assert frame.oi.tolist() == [1000, 1200]


def test_corrupt_cache_reports_failure_without_deleting_originals(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    history.collect(plan(), tmp_path, client=Client())
    raw_path = history.chunk_base(tmp_path, plan()[0]).with_suffix(".raw.json.gz")
    raw_path.write_bytes(b"damaged synthetic cache")
    with pytest.raises(RuntimeError, match="integrity"):
        history.collect(plan(), tmp_path, client=Client())
    assert raw_path.read_bytes() == b"damaged synthetic cache"
    output = (tmp_path / "history_summary.json").read_text()
    assert "CACHE_INTEGRITY_FAILED" in output
    assert '"collection_status": "PARTIAL"' in output
