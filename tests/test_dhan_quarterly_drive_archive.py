"""Quarterly Dhan exporter tests with mocked broker and Google Drive uploader."""
from datetime import date, timedelta
from pathlib import Path
import json
from types import SimpleNamespace
import zipfile

import pandas as pd
import pytest

from app.broker.dhan_historical import RollingQuery, DhanAPIError
from app.broker import dhan_quarterly_drive_archive as archive


TODAY = date(2026, 10, 9)


def test_2021_q1_q2_q3_are_outside_dhan_five_year_retention():
    plan = archive.plan_quarters(date(2021, 1, 1), TODAY, today=TODAY)
    assert len(plan) == 24
    assert [x.key for x in plan[:4]] == [
        "2021Q1", "2021Q2", "2021Q3", "2021Q4"]
    assert all(x.status == "UNAVAILABLE_BEFORE_DHAN_FIVE_YEAR_RETENTION"
               and x.zip_name is None for x in plan[:3])
    assert plan[3].eligible_from == date(2021, 10, 9)
    assert plan[3].eligible_end_exclusive == date(2022, 1, 1)
    assert plan[3].status == "PARTIAL_EARLY_RETENTION"
    assert plan[-1].key == "2026Q4"
    assert plan[-1].eligible_end_exclusive == date(2026, 10, 10)
    assert plan[-1].zip_name.endswith("through_20261009.zip")


def test_quarter_plan_is_contiguous_and_never_drops_date():
    start, end = date(2022, 1, 1), date(2023, 12, 31)
    quarters = archive.plan_quarters(start, end, today=TODAY)
    assert len(quarters) == 8
    assert quarters[0].quarter_start == start
    for a, b in zip(quarters, quarters[1:]):
        assert a.quarter_end_exclusive == b.quarter_start
    assert quarters[-1].quarter_end_exclusive == date(2024, 1, 1)


def test_plan_preview_has_no_broker_login_or_archive_files(tmp_path):
    args = options(tmp_path)
    args.from_date = date(2021, 1, 1)
    args.through = TODAY
    args.quarter = None  # Full-period preview, not the one-quarter pilot.
    result = archive.run(args, client=None)
    assert result["status"] == "PREVIEW_NO_API_CALLS"
    assert result["excluded_old_quarters"] == ["2021Q1", "2021Q2", "2021Q3"]
    assert result["estimated_readonly_dhan_calls"] == 21280  # 76 windows * 140 series * 2 intervals
    assert result["intervals_minutes"] == [1, 5]
    assert not tmp_path.joinpath("stage").exists()
    assert result["archived_quarters"] == []


def options(tmp_path):
    return SimpleNamespace(
        from_date=date(2026, 10, 6), through=TODAY,
        as_of=TODAY, quarter="2026Q4", quarter_limit=None,
        intervals=(1, 5), pause=.25, execute=False,
        progress=False, remote="dhanarchive:",
        staging=str(tmp_path / "stage"),
    )


class MockDhan:
    def __init__(self, *, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def profile(self):
        return {"dataPlan": "Active"}

    def _call(self, method, path, payload):
        assert (method, path) == ("POST", "/charts/rollingoption")
        self.calls.append(payload)
        if self.fail_on and len(self.calls) == self.fail_on:
            raise DhanAPIError("HTTP_ERROR", http_status=401)
        day = date.fromisoformat(payload["fromDate"])
        epoch = int(pd.Timestamp(
            f"{day} 09:15", tz="Asia/Kolkata").timestamp())
        interval = int(payload["interval"])
        side = "ce" if payload["drvOptionType"] == "CALL" else "pe"
        return {"data": {side: {
            "timestamp": [epoch + minute * 60 * interval for minute in range(3)],
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [100, 110, 120],
            "oi": [1000, 1010, 1020], "iv": [.2, .2, .2],
            "spot": [22400, 22410, 22420],
            "strike": [22400, 22400, 22400],
        }}}


def two_requests(q, intervals):
    for interval in intervals:
        yield RollingQuery(
            q.eligible_from, q.eligible_from + timedelta(days=1),
            expiry_flag="WEEK", expiry_code=1, strike="ATM",
            side="CALL", interval=interval)


def test_complete_quarter_one_zip_with_both_intervals_and_manifest(
        tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "_each_query", two_requests)
    observed = []

    def fake_upload(local_zip, remote):
        assert remote == "dhanarchive:"
        assert local_zip.name.endswith(".zip")
        with zipfile.ZipFile(local_zip) as z:
            names = set(z.namelist())
            assert "manifest.json" in names
            one = [name for name in names if name.startswith("1m/")]
            five = [name for name in names if name.startswith("5m/")]
            assert len(one) == 1 and len(five) == 1
            assert b"timestamp" in z.read(one[0])
            manifest = json.loads(z.read("manifest.json"))
            assert manifest["requests_planned"] == 2
            assert manifest["requests_with_successful_responses"] == 2
            assert manifest["empty_responses"] == 0
            assert manifest["rows_exported"] == 6
            assert manifest["historical_greeks_gamma_available"] is False
            assert manifest["fixed_option_contract_expiry_verified"] is False
        observed.append(local_zip.name)
        return "UPLOADED_MD5_VERIFIED"

    args = options(tmp_path)
    args.execute = True
    broker = MockDhan()
    report = archive.run(
        args, client=broker, verify_remote=False,
        sleeper=lambda _: None, uploader=fake_upload)
    assert report["status"] == "ALL_SELECTED_QUARTERS_UPLOADED"
    assert report["archived_quarters"][0]["requests"] == 2
    assert len(broker.calls) == 2
    assert {x["interval"] for x in broker.calls} == {"1", "5"}
    assert observed and not list(Path(args.staging).iterdir())


def test_interrupted_quarter_retains_stage_and_resumes_without_duplicate_calls(
        tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "_each_query", two_requests)
    first = MockDhan(fail_on=2)
    args = options(tmp_path)
    args.execute = True
    try_report = archive.run(
        args, client=first, sleeper=lambda _: None,
        verify_remote=False, uploader=lambda *_: None)
    assert try_report["status"] == "STOPPED_PARTIAL_LOCAL_STAGING_RETAINED"
    stage = list(Path(args.staging).iterdir())
    assert len(stage) == 1 and stage[0].name.endswith(".partial")
    with zipfile.ZipFile(stage[0]) as z:
        assert len([x for x in z.namelist() if x.startswith("meta/")]) == 1

    second = MockDhan()
    successful = archive.run(
        args, client=second, sleeper=lambda _: None,
        verify_remote=False,
        uploader=lambda *_: "UPLOADED_MD5_VERIFIED")
    assert successful["status"] == "ALL_SELECTED_QUARTERS_UPLOADED"
    assert len(second.calls) == 1
    assert successful["archived_quarters"][0]["new_calls"] == 1
    assert not list(Path(args.staging).iterdir())


def test_rejects_historically_unavailable_quarter(tmp_path):
    args = options(tmp_path)
    args.quarter = "2021Q1"
    args.from_date = date(2021, 1, 1)
    with pytest.raises(ValueError, match="unavailable"):
        archive.run(args)


def test_five_year_full_strategy_matches_both_intervals():
    q = archive.plan_quarters(date(2026, 10, 6),
                              date(2026, 10, 9), today=TODAY)[0]
    calls = list(archive._each_query(q, (1, 5)))
    assert len(calls) == 280  # 140 expiration/offset/side * 2 intervals
    assert sum(q.interval == 1 for q in calls) == 140
    assert sum(q.interval == 5 for q in calls) == 140


def test_drive_folder_root_is_pinned_and_token_never_shown(monkeypatch):
    captured = []
    monkeypatch.setattr(archive.shutil, "which", lambda _: "rclone")
    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(archive.subprocess, "run", fake_run)
    archive._rclone(["lsf", "dhanarchive:", "--files-only"])
    assert captured[0][-2:] == [
        "--drive-root-folder-id", archive.DRIVE_FOLDER_ID]
    archive._rclone(["listremotes"])
    assert captured[1] == ["rclone", "listremotes"]


def test_remote_checksums_disallow_silent_overwrite(tmp_path, monkeypatch):
    local = tmp_path / "archive.zip"
    local.write_bytes(b"offline fixture")
    monkeypatch.setattr(archive, "_remote_md5", lambda *args: "0" * 32)
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        archive.upload_verified(local, "dhanarchive:",
                                existing={"archive.zip"})


def test_quarter_and_date_validations():
    with pytest.raises(ValueError):
        archive.plan_quarters(date(2027, 1, 1), TODAY, today=TODAY)


def test_manifested_partial_after_interrupted_upload_can_resume_without_dhan(
        tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "_each_query", two_requests)
    args = options(tmp_path)
    args.execute = True
    first = MockDhan()
    def failed_upload(*_args):
        raise RuntimeError("simulated Drive outage")
    failure = archive.run(
        args, client=first, verify_remote=False,
        sleeper=lambda _: None, uploader=failed_upload)
    assert failure["status"] == "STOPPED_PARTIAL_LOCAL_STAGING_RETAINED"
    paths = list(Path(args.staging).iterdir())
    assert len(paths) == 1 and paths[0].name.endswith(".zip")
    # Simulate abrupt interruption after final manifest but before rename.
    staged = paths[0].with_name(paths[0].name + ".partial")
    paths[0].replace(staged)
    second = MockDhan()
    report = archive.run(
        args, client=second, verify_remote=False, sleeper=lambda _: None,
        uploader=lambda *_: "UPLOADED_MD5_VERIFIED")
    assert report["status"] == "ALL_SELECTED_QUARTERS_UPLOADED"
    assert report["archived_quarters"][0]["new_calls"] == 0
    assert report["archived_quarters"][0]["resumed_completed_zip"] is True
    assert len(second.calls) == 0
    assert not list(Path(args.staging).iterdir())


def test_empty_dhan_interval_is_disclosed_not_claimed_complete(
        tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "_each_query", two_requests)
    class EmptyForFiveMinute(MockDhan):
        def _call(self, method, path, payload):
            if payload["interval"] == "5":
                self.calls.append(payload)
                return {"data": {"ce": None}}
            return super()._call(method, path, payload)
    observed = []
    def check_upload(file, remote):
        with zipfile.ZipFile(file) as z:
            meta = json.loads(z.read("manifest.json"))
            observed.append(meta)
        return "UPLOADED_MD5_VERIFIED"
    args = options(tmp_path)
    args.execute = True
    report = archive.run(
        args, client=EmptyForFiveMinute(), verify_remote=False,
        sleeper=lambda _: None, uploader=check_upload)
    assert report["status"] == "ALL_SELECTED_QUARTERS_UPLOADED"
    assert observed[0]["empty_responses"] == 1
    assert observed[0]["coverage_status"] == "PARTIAL_EMPTY_RESPONSES"
    assert report["all_requested_quarters_covered"] is False
