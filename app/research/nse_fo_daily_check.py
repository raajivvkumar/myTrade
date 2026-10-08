"""Official NSE 2024 legacy F&O DAILY bhavcopy reference, read-only and memory-only.

NSE EOD values can corroborate exact OPTIDX/NIFTY/expiry/strike/side
identity and daily OHLC/OI. They CANNOT verify historic 1-minute bars,
individual legacy seconds, bid/ask, IV, Gamma or a 5x executed trade.
No files are saved; explicit request only.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO
import zipfile

import pandas as pd
import requests

HOSTS = ("archives.nseindia.com", "nsearchives.nseindia.com")
NEEDED = {
    "INSTRUMENT", "SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP",
    "OPEN", "HIGH", "LOW", "CLOSE", "CONTRACTS", "OPEN_INT", "TIMESTAMP",
}
MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_CSV_BYTES = 128 * 1024 * 1024


def _archive_path(day: date) -> str:
    if not isinstance(day, date) or not date(2024, 1, 1) <= day <= date(2024, 7, 5):
        raise ValueError("This validator supports only NSE's Jan–5 Jul 2024 legacy F&O bhavcopy.")
    month = day.strftime("%b").upper()
    return (f"/content/historical/DERIVATIVES/{day.year}/{month}/"
            f"fo{day.day:02d}{month}{day.year}bhav.csv.zip")


def official_bhavcopy_urls(day: date) -> list[str]:
    path = _archive_path(day)
    return ["https://" + host + path for host in HOSTS]


def fetch_nse_daily_fo(day: date, *, session=None) -> pd.DataFrame:
    """One NSE daily ZIP in RAM; fallback only between NSE official hosts."""
    urls = official_bhavcopy_urls(day)
    client = session if session is not None else requests.Session()
    failures = []
    for url in urls:
        try:
            resp = client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                    "Accept": "application/zip,application/octet-stream,*/*",
                    "Referer": "https://www.nseindia.com/all-reports-derivatives",
                },
                timeout=20,
            )
        except requests.RequestException:
            failures.append("NSE host network failure")
            continue
        if resp.status_code != 200:
            failures.append(f"NSE HTTP {resp.status_code}")
            continue
        payload = resp.content
        if len(payload) > MAX_ZIP_BYTES:
            raise ValueError("NSE daily archive exceeds accepted ZIP size")
        try:
            with zipfile.ZipFile(BytesIO(payload)) as package:
                members = [x for x in package.infolist()
                           if x.filename.lower().endswith(".csv")
                           and not x.is_dir()]
                if len(members) != 1 or members[0].file_size > MAX_CSV_BYTES:
                    raise ValueError("Unexpected NSE archive entries/size")
                with package.open(members[0]) as entry:
                    csv_bytes = entry.read(MAX_CSV_BYTES + 1)
                if len(csv_bytes) > MAX_CSV_BYTES:
                    raise ValueError("NSE CSV exceeds accepted size")
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise ValueError("Malformed NSE bhavcopy ZIP") from exc
        frame = pd.read_csv(BytesIO(csv_bytes), low_memory=False, encoding="utf-8-sig")
        frame.columns = [str(c).strip().upper() for c in frame.columns]
        missing = NEEDED - set(frame.columns)
        if missing:
            raise ValueError("NSE bhavcopy missing required fields: " + ",".join(sorted(missing)))
        dates = pd.to_datetime(frame["TIMESTAMP"].astype(str).str.strip(),
                               format="%d-%b-%Y", errors="coerce").dt.date
        if dates.isna().any() or set(dates) != {day}:
            raise ValueError("NSE bhavcopy timestamp mismatch / unexpected data date")
        frame["TRADE_DATE"] = dates
        frame.attrs["nse_original_url"] = url
        frame.attrs["nse_source_status"] = "OFFICIAL_DAILY_REFERENCE_ONLY"
        return frame
    raise RuntimeError("Could not retrieve official NSE 2024 daily FO ZIP; " +
                       ", ".join(failures) +
                       ". Try official NSE website in your browser; no data substituted.")


def select_nifty_options(frame: pd.DataFrame, *, expiry: date,
                         strikes=(21450, 21500, 21550, 21600),
                         side="PE") -> pd.DataFrame:
    """One actual exchange-listed side, expiry and strike per EOD row."""
    if not isinstance(expiry, date) or side not in ("CE", "PE"):
        raise ValueError("Invalid option expiry or side")
    missing = NEEDED - set(frame.columns)
    if missing:
        raise ValueError("Incomplete daily NSE bhavcopy")
    exps = pd.to_datetime(frame.EXPIRY_DT.astype(str).str.strip(),
                          format="%d-%b-%Y", errors="coerce").dt.date
    price = pd.to_numeric(frame.STRIKE_PR, errors="coerce")
    selected = frame.loc[
        frame.INSTRUMENT.astype(str).str.strip().eq("OPTIDX")
        & frame.SYMBOL.astype(str).str.strip().eq("NIFTY")
        & exps.eq(expiry)
        & price.isin(strikes)
        & frame.OPTION_TYP.astype(str).str.strip().eq(side)
    ].copy()
    if selected.empty:
        return selected
    selected["EXPIRY"] = expiry.isoformat()
    selected["STRIKE"] = pd.to_numeric(selected.STRIKE_PR)
    selected["SIDE"] = side
    selected["SOURCE_STATUS"] = "OFFICIAL_DAILY_REFERENCE_NOT_MINUTE_VALIDATION"
    for k in ("OPEN", "HIGH", "LOW", "CLOSE", "CONTRACTS", "OPEN_INT"):
        selected[k] = pd.to_numeric(selected[k], errors="coerce")
    if selected[["OPEN", "HIGH", "LOW", "CLOSE"]].isna().any().any():
        raise ValueError("Invalid official daily option OHLC")
    if selected.duplicated(["TRADE_DATE", "EXPIRY", "STRIKE", "SIDE"]).any():
        raise ValueError("Duplicate official exchange option contract/date")
    return selected[
        ["TRADE_DATE", "EXPIRY", "STRIKE", "SIDE", "OPEN", "HIGH",
         "LOW", "CLOSE", "CONTRACTS", "OPEN_INT", "SOURCE_STATUS"]
    ].sort_values(["TRADE_DATE", "STRIKE"]).reset_index(drop=True)


def official_four_pe_reference(*, session=None) -> tuple[pd.DataFrame, list[str]]:
    """Explicit two-day NSE request, all results remain in memory."""
    frames, urls = [], []
    for d in (date(2024, 4, 3), date(2024, 4, 4)):
        day = fetch_nse_daily_fo(d, session=session)
        selected = select_nifty_options(day, expiry=date(2024, 4, 4))
        frames.append(selected)
        urls.append(day.attrs["nse_original_url"])
    return pd.concat(frames, ignore_index=True), urls
