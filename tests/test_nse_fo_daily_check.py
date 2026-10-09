"""NSE public daily F&O reference: synthetic mocked ZIPs, no real network."""
from datetime import date
from io import BytesIO
import zipfile

import pandas as pd
import pytest

from app.research.nse_fo_daily_check import (
    fetch_nse_daily_fo, official_bhavcopy_urls,
    official_four_pe_reference, select_nifty_options,
)


def fake_csv(day):
    return (
        "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,"
        "SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
        + "".join(
            f"OPTIDX,NIFTY,04-Apr-2024,{strike},PE,1.0,2.0,0.05,0.5,"
            f"0.5,400,5.0,20000,500,{day}\n"
            for strike in (21450, 21500, 21550, 21600)
        )
        + f"OPTIDX,NIFTY,04-Apr-2024,21500,CE,100,110,99,103,103,200,10,2000,5,{day}\n"
        + f"OPTIDX,BANKNIFTY,04-Apr-2024,21500,PE,0.2,0.4,0.1,0.15,0.15,400,5,3000,5,{day}\n"
    )


def zip_payload(csv_text):
    out = BytesIO()
    with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("fo04APR2024bhav.csv", csv_text)
    return out.getvalue()


class Response:
    def __init__(self, content, status=200):
        self.status_code = status
        self.content = content


class Session:
    def __init__(self, fail_first=False, bad_date=False):
        self.calls = []
        self.fail_first = fail_first
        self.bad_date = bad_date

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.fail_first and url.startswith("https://archives."):
            return Response(b"forbidden", 403)
        day = "03-Apr-2024" if "03APR" in url else "04-Apr-2024"
        if self.bad_date:
            day = "01-Apr-2024"
        return Response(zip_payload(fake_csv(day)))


def test_official_2024_archive_url_exact():
    urls = official_bhavcopy_urls(date(2024, 4, 3))
    assert len(urls) == 2
    assert urls[0].endswith("/2024/APR/fo03APR2024bhav.csv.zip")
    assert all("nseindia.com" in url for url in urls)


def test_no_unverified_dates_accepted():
    with pytest.raises(ValueError, match="2024"):
        official_bhavcopy_urls(date(2023, 4, 3))
    with pytest.raises(ValueError, match="2024"):
        official_bhavcopy_urls(date(2024, 7, 8))


def test_daily_nse_csv_validates_date_and_origin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Session()
    frame = fetch_nse_daily_fo(date(2024, 4, 4), session=s)
    assert frame.attrs["nse_source_status"] == "OFFICIAL_DAILY_REFERENCE_ONLY"
    assert all(frame.TRADE_DATE == date(2024, 4, 4))
    assert len(s.calls) == 1
    assert not list(tmp_path.iterdir())


def test_one_exact_expiry_side_strike_and_underlying():
    frame = fetch_nse_daily_fo(date(2024, 4, 3), session=Session())
    filtered = select_nifty_options(frame, expiry=date(2024, 4, 4))
    assert len(filtered) == 4
    assert set(filtered.STRIKE) == {21450, 21500, 21550, 21600}
    assert set(filtered.SIDE) == {"PE"}
    assert set(filtered.EXPIRY) == {"2024-04-04"}
    assert all(filtered.SOURCE_STATUS == "OFFICIAL_DAILY_REFERENCE_NOT_MINUTE_VALIDATION")


def test_two_eod_days_remain_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Session()
    df, urls = official_four_pe_reference(session=s)
    assert len(df) == 8
    assert len(urls) == 2
    assert df.TRADE_DATE.nunique() == 2
    assert not list(tmp_path.iterdir())


def test_official_host_fallback_only():
    s = Session(fail_first=True)
    frame = fetch_nse_daily_fo(date(2024, 4, 4), session=s)
    assert len(s.calls) == 2
    assert s.calls[-1].startswith("https://nsearchives.nseindia.com/")
    assert frame.attrs["nse_original_url"] == s.calls[-1]


def test_unavailable_daily_does_not_make_up_prices():
    class FailSession:
        def get(self, url, **kwargs):
            return Response(b"unavailable", status=403)
    with pytest.raises(RuntimeError, match="Could not retrieve"):
        fetch_nse_daily_fo(date(2024, 4, 3), session=FailSession())


def test_reject_mismatched_trade_date():
    with pytest.raises(ValueError, match="timestamp mismatch"):
        fetch_nse_daily_fo(date(2024, 4, 3), session=Session(bad_date=True))


def test_reject_unsupported_html_or_nonzip():
    class BadSession:
        def get(self, url, **kwargs):
            return Response(b"<html>not a zip</html>")
    with pytest.raises(ValueError, match="Malformed"):
        fetch_nse_daily_fo(date(2024, 4, 4), session=BadSession())
