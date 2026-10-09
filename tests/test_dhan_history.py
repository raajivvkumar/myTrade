"""Offline five-year plan, integrity, coverage and research-claim checks."""
from datetime import date, datetime
import json
import gzip

import pandas as pd
import pytest

from app.broker import dhan_history as history


def plan():
    return history.make_plan(date(2026, 10, 8), date(2026, 10, 9), ["ATM"], "WEEK", 1)


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
    entries = history.make_plan(beginning, through, ["ATM"], "WEEK", 1)
    assert len(entries) == 183
    windows = entries[::3]
    assert windows[0]["end"] == "2026-10-10"
    assert windows[-1]["start"] == "2021-10-09"
    assert all(date.fromisoformat(x["end"])-date.fromisoformat(x["start"]) <= pd.Timedelta(days=30)
               for x in windows)
    assert all(windows[i]["start"] == windows[i+1]["end"] for i in range(len(windows)-1))
    assert entries[0]["series"] == "INDEX"
    assert entries[1]["payload"]["drvOptionType"] == "CALL"
    assert entries[1]["payload"]["expiryCode"] == 1
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
        history.make_plan(date(2026, 10, 8), date(2026, 10, 9), strikes, "WEEK", 1)


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


def test_index_only_uses_same_index_entries_and_keeps_latest_first():
    full = history.make_plan(date(2021, 10, 9), date(2026, 10, 9), ["ATM"], "WEEK", 1)
    indices = history.make_plan(date(2021, 10, 9), date(2026, 10, 9), ["ATM"], "WEEK", 1,
                                index_only=True)
    assert len(indices) == 61
    assert indices == full[::3]


def test_option_api_failure_after_cached_index_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    history.collect(plan(), tmp_path, client=Client(), max_requests=1)
    class OptionFailure(Client):
        def _call(self, method, endpoint, payload):
            assert endpoint == "/charts/rollingoption"
            self.calls.append((method, endpoint))
            raise history.DhanAPIError("HTTP_ERROR", http_status=400, provider_code="DH-905")
    client = OptionFailure()
    with pytest.raises(history.BackfillError) as raised:
        history.collect(plan(), tmp_path, client=client)
    error = raised.value.details
    assert error["series"] == "WEEK_1_ATM_CALL"
    assert error["stage"] == "REQUEST"
    assert error["http_status"] == 400
    assert error["provider_code"] == "DH-905"
    report = json.loads((tmp_path / "history_summary.json").read_text())
    assert report["completed_chunks"] == 1
    assert report["errors"] == [error]
    # Index-only progress must reuse the original index and retain the option failure.
    calls = Client()
    final = history.collect(plan()[::3], tmp_path, client=calls)
    assert calls.calls == ["profile"]
    assert final["collection_status"] == "FINISHED"
    assert final["scope"] == "NIFTY 50 index only"
    assert json.loads((tmp_path / "last_failure.json").read_text())["error"] == error


def test_validation_failure_identifies_rule_without_saving_bad_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    class InvalidBars(Client):
        def _call(self, method, endpoint, payload):
            raw = response()
            raw["low"][0] = 200
            return raw
    with pytest.raises(history.BackfillError) as raised:
        history.collect(plan(), tmp_path, client=InvalidBars())
    assert raised.value.details["stage"] == "CANDLE_VALIDATION"
    assert raised.value.details["validation_rule"] == "Invalid OHLC range"
    assert not list(tmp_path.rglob("*.parquet"))


def test_storage_failure_keeps_safe_errno_not_secret_path(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    def full_disk(*args):
        raise OSError(28, "synthetic-secret-path")
    monkeypatch.setattr(history, "save_chunk", full_disk)
    with pytest.raises(history.BackfillError) as raised:
        history.collect(plan(), tmp_path, client=Client())
    assert raised.value.details["stage"] == "CHUNK_STORAGE"
    assert raised.value.details["errno"] == 28
    assert "synthetic-secret" not in (tmp_path / "last_failure.json").read_text()


def test_checkpoint_failure_does_not_hide_primary_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    def locked_report(*args):
        raise OSError(13, "synthetic-secret-path")
    monkeypatch.setattr(history, "write_report", locked_report)
    with pytest.raises(history.BackfillError) as raised:
        history.collect(plan(), tmp_path, client=Client(fail=True))
    assert raised.value.details["stage"] == "REQUEST"
    assert raised.value.details["summary_write_failed"] is True
    assert "synthetic-secret" not in str(raised.value)


def test_cli_prints_safe_structured_failure(monkeypatch, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 9, 12, tzinfo=tz)
    monkeypatch.setattr(history, "datetime", Clock)
    monkeypatch.setattr(history, "load_local_credentials", lambda: None)
    monkeypatch.setattr("sys.argv", ["history", "--through", "2026-10-09", "--execute"])
    details = dict(stage="REQUEST", series="WEEK_1_ATM_CALL", http_status=400, provider_code="DH-905")
    def fail(*args, **kwargs):
        raise history.BackfillError(details, "synthetic-secret-error")
    monkeypatch.setattr(history, "collect", fail)
    with pytest.raises(SystemExit) as raised:
        history.main()
    assert '"provider_code": "DH-905"' in str(raised.value)
    assert "synthetic-secret" not in str(raised.value)


def test_backfill_cli_default_uses_near_code_one(monkeypatch, capsys):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 9, 12, tzinfo=tz)
    monkeypatch.setattr(history, "datetime", Clock)
    monkeypatch.setattr("sys.argv", ["history", "--through", "2026-10-09"])
    history.main()
    report = json.loads(capsys.readouterr().out)
    assert report["newest"][1]["payload"]["expiryCode"] == 1
    assert report["newest"][1]["series"] == "WEEK_1_ATM_CALL"


def test_backfill_cli_code_zero_rejected_before_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid expiry code must not read credentials")
    monkeypatch.setattr(history, "load_local_credentials", forbidden)
    monkeypatch.setattr("sys.argv", ["history", "--expiry-code", "0", "--execute"])
    with pytest.raises(SystemExit) as raised:
        history.main()
    assert raised.value.code == 2


def test_expiry_correction_reuses_index_but_does_not_rename_zero_option_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    entries = plan()
    old_index = entries[0]
    # Index request is independent of the option expiry selector.
    legacy_call = dict(entries[1], series="WEEK_0_ATM_CALL",
                       payload=dict(entries[1]["payload"], expiryCode=0))
    history.save_chunk(tmp_path, old_index, response(),
                       history.parse_bars(response(), old_index))
    legacy_raw = response(True)
    legacy_path = history.chunk_base(tmp_path, legacy_call).with_suffix(".parquet")
    history.save_chunk(tmp_path, legacy_call, legacy_raw,
                       history.parse_bars(legacy_raw, legacy_call))
    legacy_hash = history.file_hash(legacy_path)
    report = history.collect(entries, tmp_path, client=Client())
    assert report["completed_chunks"] == 3
    assert history.file_hash(legacy_path) == legacy_hash
    assert all(item["series"] != "WEEK_0_ATM_CALL" for item in report["yearly"])


def rolling_response_at(times):
    n = len(times)
    source = dict(
        timestamp=[int(pd.Timestamp(t, tz="Asia/Kolkata").timestamp()) for t in times],
        open=[100+i for i in range(n)], high=[102+i for i in range(n)],
        low=[99+i for i in range(n)], close=[101+i for i in range(n)],
        volume=[20]*n, strike=[22500]*n, iv=[20]*n, oi=[1000]*n, spot=[22510]*n,
    )
    return {"data": {"ce": source, "pe": None}}


def reported_window_entries():
    return history.make_plan(date(2026, 8, 11), date(2026, 10, 9), ["ATM"], "WEEK", 1)


def test_rolling_end_date_bars_excluded_and_retained_in_original_response(tmp_path):
    entry = reported_window_entries()[4]  # older CALL window: Aug 11 to Sep 10
    raw = rolling_response_at(["2026-09-09 15:29", "2026-09-10 09:15", "2026-09-10 09:16"])
    frame = history.parse_bars(raw, entry)
    assert frame.timestamp.tolist() == [pd.Timestamp("2026-09-09 15:29")]
    assert frame.attrs["window_validation"]["excluded_end_date_rows"] == 2
    assert frame.attrs["window_validation"]["returned_rows"] == 3
    manifest = history.save_chunk(tmp_path, entry, raw, frame)
    assert manifest["rows"] == 1
    assert manifest["window_validation"]["excluded_end_date_rows"] == 2
    raw_path = history.chunk_base(tmp_path, entry).with_suffix(".raw.json.gz")
    with gzip.open(raw_path, "rt", encoding="utf-8") as source:
        assert json.load(source) == raw


@pytest.mark.parametrize("outside", ["2026-08-10 15:29", "2026-09-11 09:15"])
def test_unexpected_dates_still_fail_with_precise_safe_bounds(outside):
    entry = reported_window_entries()[4]
    times = sorted(["2026-08-11 09:15", outside])
    raw = rolling_response_at(times)
    with pytest.raises(history.CandleWindowError) as raised:
        history.parse_bars(raw, entry)
    details = raised.value.details
    assert details["before_start_rows"] + details["after_end_date_rows"] == 1
    assert details["returned_rows"] == 2
    assert details["requested_end_exclusive_ist"] == "2026-09-10T00:00:00+05:30"


def test_index_window_does_not_get_rolling_end_date_tolerance():
    entry = reported_window_entries()[3]
    raw = rolling_response_at(["2026-09-09 15:29", "2026-09-10 09:15"])["data"]["ce"]
    with pytest.raises(history.CandleWindowError):
        history.parse_bars(raw, entry)


def test_adjacent_rolling_windows_do_not_duplicate_end_date():
    entries = reported_window_entries()
    old = history.parse_bars(rolling_response_at(["2026-09-09 15:29", "2026-09-10 09:15"]), entries[4])
    new = history.parse_bars(rolling_response_at(["2026-09-10 09:15", "2026-10-09 15:29"]), entries[1])
    assert not pd.concat([old, new]).timestamp.duplicated().any()


def test_only_boundary_day_response_is_empty_with_visible_exclusion_count(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    entry = reported_window_entries()[4]
    raw = rolling_response_at(["2026-09-10 09:15"])
    class BoundaryOnly(Client):
        def _call(self, *args):
            return raw
    report = history.collect([entry], tmp_path, client=BoundaryOnly())
    assert report["rows"] == 0
    assert report["empty_chunks"] == 1
    assert report["excluded_end_date_rows"] == 1
    assert report["collection_status"] == "FINISHED_WITH_EMPTY_WINDOWS"
    assert history.load_cached(tmp_path, entry)["window_validation"]["included_rows"] == 0


def test_window_error_report_contains_timestamps_without_broker_free_text(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    entry = reported_window_entries()[4]
    class Outside(Client):
        def _call(self, *args):
            return rolling_response_at(["2026-08-10 15:29", "2026-08-11 09:15"])
    with pytest.raises(history.BackfillError) as raised:
        history.collect([entry], tmp_path, client=Outside())
    bounds = raised.value.details["window_diagnostics"]
    assert bounds["first_returned_timestamp_ist"] == "2026-08-10T15:29:00+05:30"
    assert bounds["last_returned_timestamp_ist"] == "2026-08-11T09:15:00+05:30"
    assert json.loads((tmp_path / "last_failure.json").read_text())["error"]["window_diagnostics"] == bounds


@pytest.mark.parametrize("field,value", [("volume", -2), ("oi", -3), ("iv", -1)])
def test_negative_optional_cell_masks_only_that_feature_and_preserves_raw(tmp_path, field, value):
    entry = plan()[1]
    raw = response(True)
    raw["data"]["ce"][field][0] = value
    frame = history.parse_bars(raw, entry)
    assert len(frame) == 2
    assert frame[["open", "high", "low", "close"]].values.tolist() == [
        [100, 102, 99, 101], [101, 103, 100, 102]]
    assert pd.isna(frame[field].iloc[0])
    assert frame[field].iloc[1] == raw["data"]["ce"][field][1]
    assert frame[f"{field}_invalid_negative"].tolist() == [True, False]
    quality = frame.attrs["optional_field_quality"][field]
    assert quality == dict(source_negative_rows=1, included_negative_rows=1,
                           first_source_negative_timestamp_ist="2026-10-08T09:16:00+05:30",
                           source_minimum_negative_value=value)
    assert raw["data"]["ce"][field][0] == value
    manifest = history.save_chunk(tmp_path, entry, raw, frame)
    assert manifest["optional_field_quality"][field] == quality
    base = history.chunk_base(tmp_path, entry)
    with gzip.open(base.with_suffix(".raw.json.gz"), "rt", encoding="utf-8") as source:
        assert json.load(source) == raw
    stored = pd.read_parquet(base.with_suffix(".parquet"))
    assert pd.isna(stored[field].iloc[0])
    assert stored[f"{field}_invalid_negative"].tolist() == [True, False]
    facts = manifest["daily"][0]
    assert facts[f"{field}_negative_rows"] == 1
    if field in ("oi", "iv"):
        assert facts[f"{field}_available_rows"] == 1


def test_zero_optional_values_and_absent_values_are_not_negative():
    raw = response(True)
    raw["data"]["ce"]["volume"] = [0, 30]
    raw["data"]["ce"]["oi"] = [0, 1200]
    raw["data"]["ce"]["iv"] = [0, None]
    frame = history.parse_bars(raw, plan()[1])
    assert frame.loc[0, ["volume", "oi", "iv"]].tolist() == [0, 0, 0]
    assert pd.isna(frame.iv.iloc[1])
    for field in history.OPTIONAL_NONNEGATIVE:
        assert not frame[f"{field}_invalid_negative"].any()
        assert frame.attrs["optional_field_quality"][field]["source_negative_rows"] == 0
        assert frame.attrs["optional_field_quality"][field]["source_minimum_negative_value"] is None


def test_negative_open_interest_alias_is_flagged_for_index():
    raw = response()
    raw["open_interest"] = [-1, 1200]
    frame = history.parse_bars(raw, plan()[0])
    assert pd.isna(frame.oi.iloc[0])
    assert frame.oi_invalid_negative.tolist() == [True, False]
    assert raw["open_interest"] == [-1, 1200]


@pytest.mark.parametrize("field", ["volume", "oi", "iv"])
def test_nonfinite_optional_values_still_reject_entire_response(field):
    raw = response(True)
    raw["data"]["ce"][field][0] = float("inf")
    with pytest.raises(ValueError, match="Invalid numeric candle values"):
        history.parse_bars(raw, plan()[1])


def test_negative_optional_values_do_not_bypass_invalid_ohlc():
    raw = response(True)
    raw["data"]["ce"]["iv"][0] = -1
    raw["data"]["ce"]["low"][0] = 200
    with pytest.raises(ValueError, match="Invalid OHLC range"):
        history.parse_bars(raw, plan()[1])


def test_negative_feature_on_excluded_boundary_is_not_counted_in_logical_data(tmp_path):
    entry = reported_window_entries()[4]
    raw = rolling_response_at(["2026-09-09 15:29", "2026-09-10 09:15"])
    raw["data"]["ce"]["iv"][1] = -2
    frame = history.parse_bars(raw, entry)
    manifest = history.save_chunk(tmp_path, entry, raw, frame)
    quality = manifest["optional_field_quality"]["iv"]
    assert quality["source_negative_rows"] == 1
    assert quality["included_negative_rows"] == 0
    assert frame.iv.tolist() == [20]
    assert manifest["daily"][0]["iv_available_rows"] == 1
    assert manifest["daily"][0]["iv_negative_rows"] == 0
    report = history.write_report(tmp_path, [entry], [manifest], [])
    assert report["optional_field_quality"]["iv"] == dict(source_negative_rows=1, included_negative_rows=0)


def test_optional_quality_report_mixes_old_cache_with_new_rows_and_resumes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    entries = plan()[:2]
    index = history.save_chunk(tmp_path, entries[0], response(),
                              history.parse_bars(response(), entries[0]))
    # Simulate an already-validated v1 manifest produced before quality fields.
    index.pop("optional_field_quality")
    for row in index["daily"]:
        for field in history.OPTIONAL_NONNEGATIVE:
            row.pop(f"{field}_negative_rows")
    history.atomic_json(history.chunk_base(tmp_path, entries[0]).with_suffix(".manifest.json"), index)
    class NegativeFeatures(Client):
        def _call(self, *args):
            raw = response(True)
            raw["data"]["ce"]["volume"] = [-2, 30]
            raw["data"]["ce"]["oi"] = [-3, 1200]
            raw["data"]["ce"]["iv"] = [-1, -5]
            return raw
    report = history.collect(entries, tmp_path, client=NegativeFeatures())
    assert report["collection_status"] == "FINISHED"
    assert report["rows"] == 4
    assert report["optional_field_quality"] == {
        "volume": dict(source_negative_rows=1, included_negative_rows=1),
        "oi": dict(source_negative_rows=1, included_negative_rows=1),
        "iv": dict(source_negative_rows=2, included_negative_rows=2),
    }
    yearly = {item["series"]: item for item in report["yearly"]}
    assert yearly["INDEX"]["iv_negative_rows"] == 0
    assert yearly["WEEK_1_ATM_CALL"]["iv_negative_rows"] == 2
    assert yearly["WEEK_1_ATM_CALL"]["iv_available_rows"] == 0
    assert yearly["WEEK_1_ATM_CALL"]["oi_available_rows"] == 1
    daily = pd.read_csv(tmp_path / "daily_quality.csv")
    assert daily.iv_negative_rows.tolist() == [0, 2]
    assert "negative_optional_rows=" in capsys.readouterr().err
    retry = Client()
    repeated = history.collect(entries, tmp_path, client=retry)
    assert retry.calls == ["profile"]
    assert repeated["optional_field_quality"] == report["optional_field_quality"]
    assert repeated["fixed_contract_multiplier_event_count"] is None
