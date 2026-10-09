"""Offline tests: Upstox index V3 read-only, safety and provenance."""
from argparse import Namespace
from datetime import date

import pytest

from app.broker.upstox_index_history import UpstoxIndexClient, validate_index_candles
from app.broker.upstox_index_cli import make_windows, make_path, run


def sample():
    return {"status": "success", "data": {"candles": [
        ["2026-03-23T09:16:00+05:30", 23001, 23010, 22990, 23008, 0, 0],
        ["2026-03-23T09:15:00+05:30", 23000, 23005, 22995, 23001, 0, 0],
    ]}}


def test_cash_index_ohlc_accepts_zero_oi_volume_and_ist():
    frame = validate_index_candles(sample(), start=date(2026, 3, 23),
                                   end=date(2026, 3, 24))
    assert frame.close.tolist() == [23001, 23008]
    assert frame.volume.tolist() == [0, 0]
    assert frame.timestamp.dt.tz is None
    assert frame.instrument_key.nunique() == 1
    assert frame.data_quality.eq("UNVALIDATED_UPSTOX_INDEX_V3").all()


def test_invalid_data_fails_closed():
    x = sample()
    x["data"]["candles"][0][3] = 23020
    with pytest.raises(ValueError, match="OHLC"):
        validate_index_candles(x, start=date(2026, 3, 23),
                               end=date(2026, 3, 24))
    x = sample()
    x["data"]["candles"].append(x["data"]["candles"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_index_candles(x, start=date(2026, 3, 23),
                               end=date(2026, 3, 24))


def test_single_month_safe_windows_are_contiguous():
    windows = list(make_windows(date(2026, 1, 1), date(2026, 3, 24)))
    assert windows[0] == (date(2026, 1, 1), date(2026, 1, 28))
    assert windows[-1][1] == date(2026, 3, 24)
    assert all((end - start).days <= 27 for start, end in windows)
    assert all(windows[i][1].toordinal() + 1 == windows[i+1][0].toordinal()
               for i in range(len(windows)-1))


def test_read_only_v3_url_and_no_token_leak():
    class Good:
        status_code = 200
        def json(self):
            return sample()
    class Fake:
        def get(self, url, **kwargs):
            assert url.startswith("https://api.upstox.com/v3/historical-candle/")
            assert "NSE_INDEX%7CNifty%2050/minutes/1/" in url
            assert kwargs["headers"]["Authorization"] == "Bearer SECRET"
            return Good()
    client = UpstoxIndexClient(token="SECRET", session=Fake())
    result = client.history(start=date(2026, 3, 23), end=date(2026, 3, 24))
    assert len(result) == 2


def test_default_dry_run_needs_no_token(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    args = Namespace(from_date=date(2026, 3, 23), to_date=date(2026, 3, 24),
                     index="NIFTY", minutes=1, output_dir=str(tmp_path),
                     execute=False, max_windows=1, pause_seconds=1.0)
    run(args)
    assert "DRY RUN" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_mass_download_requires_explicit_approval(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    args = Namespace(from_date=date(2026, 1, 1), to_date=date(2026, 3, 24),
                     index="NIFTY", minutes=1, output_dir=str(tmp_path),
                     execute=True, max_windows=1, pause_seconds=1.0)
    with pytest.raises(ValueError, match="max-windows"):
        run(args)


def test_sample_path_not_same_as_expired_option_archive(tmp_path):
    path = make_path(tmp_path, "NIFTY", date(2026, 3, 23),
                     date(2026, 3, 24), 1)
    assert str(path).endswith("2026-03-23_to_2026-03-24_inclusive.parquet")
    assert "1minute" in path.parts
