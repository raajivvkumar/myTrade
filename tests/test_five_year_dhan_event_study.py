"""Five-year Dhan query planning tests; synthetic and offline only."""
from datetime import date

import pytest

from app.research.dhan_direct_ram_study import queries
from app.research.dhan_event_discovery_v3 import run
from app.research.five_year_ram_accumulators import StreamingRollingSummary
from app.research.option_multiplier_event_scan import (
    scan_rolling_frame, summarize_scans,
)
from tests.test_option_multiplier_event_scan import bars, options, MockDhan


def five_year_opts(**overrides):
    obj = options()
    obj.from_date = date(2021, 10, 9)
    obj.through = date(2026, 10, 9)
    obj.full = True
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


def test_full_five_year_plan_is_61_windows_8540_requests():
    planned = list(queries(date(2021, 10, 9), date(2026, 10, 9), full=True))
    assert len(planned) == 8540
    assert len({(q.start, q.end) for q in planned}) == 61
    assert planned[0].end == date(2026, 10, 10)
    assert planned[-1].start == date(2021, 10, 9)


def test_full_five_year_preview_requires_no_api_calls():
    client = MockDhan()
    report = run(five_year_opts(max_requests=None), client=client)
    assert report["status"] == "PREVIEW_NO_API_CALLS"
    assert report["planned_api_requests"] == 8540
    assert report["max_requests"] is None
    assert report["requested_start_date"] == "2021-10-09"
    assert report["requested_end_date_inclusive"] == "2026-10-09"
    assert client.called == 0


def test_partial_one_call_does_not_claim_five_year_data():
    report = run(five_year_opts(execute=True, max_requests=1),
                 client=MockDhan(), sleeper=lambda _: None)
    assert report["planned_api_requests"] == 8540
    assert report["completed_api_requests"] == 1
    assert report["status"] == "PARTIAL_MAX_REQUESTS"
    assert report["coverage_fraction_of_requested_calls"] < 0.001
    assert not report["covered_entire_requested_universe"]
    assert report["market_files_saved"] == 0


def test_last_request_offset_never_claims_complete_history():
    class EmptyDhan:
        def __init__(self):
            self.calls = []
        def profile(self):
            return {"dataPlan": "Active"}
        def _call(self, method, path, payload):
            self.calls.append(payload)
            return {"data": {"ce": None, "pe": None}}
    opts = five_year_opts(execute=True, max_requests=1, start_request=8539)
    mock = EmptyDhan()
    report = run(opts, client=mock, sleeper=lambda _: None)
    assert len(mock.calls) == 1
    assert report["status"] == "PARTIAL_SKIPPED_EARLIER_REQUESTS"
    assert report["skipped_prior_api_requests"] == 8539
    assert report["next_request_index_zero_based"] == 8540
    assert report["empty_responses"] == 1
    assert not report["covered_entire_requested_universe"]


def test_out_of_range_offset_is_rejected():
    with pytest.raises(ValueError, match="start-request"):
        run(five_year_opts(start_request=8540))


def test_streamed_counts_match_batch_counts():
    first = scan_rolling_frame(
        bars(n=100, jump=50), series="WEEK_1_ATM_CALL", side="CALL", horizon=60)
    second = scan_rolling_frame(
        bars(n=90), series="WEEK_2_ATM_CALL", side="CALL", horizon=30)
    baseline = summarize_scans([first, second], max_examples=4)
    rolling = StreamingRollingSummary(max_examples=4)
    rolling.add(first)
    rolling.add(second)
    result = rolling.result()
    assert result["bars_analysed"] == baseline["bars_analysed"]
    for threshold in ("2x", "3x", "5x", "10x"):
        assert result["thresholds"][threshold] == baseline["thresholds"][threshold]
        assert len(result["examples"][threshold]["records"]) <= 4
