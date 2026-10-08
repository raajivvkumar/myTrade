"""Offline Upstox tests; no real credentials or network used."""
from argparse import Namespace
from datetime import date
import pytest
from app.broker.upstox_historical import NIFTY, UpstoxClient, validate_contract, find_contract, normalize_candles
from app.broker.upstox_cli import output_for, run

D = date(2026, 3, 24)


def raw_contract():
    return dict(instrument_key="NSE_FO|47982|24-03-2026",
                trading_symbol="NIFTY 23000 PE 24 MAR 26", expiry="2026-03-24",
                strike_price=23000, instrument_type="PE", lot_size=65,
                underlying_key=NIFTY)


def sample():
    return {"status": "success", "data": {"candles": [
        ["2026-03-23T09:16:00+05:30", 5, 7, 4, 6, 100, 2500],
        ["2026-03-23T09:15:00+05:30", 4, 6, 3, 5, 90, 2400],
    ]}}


def test_contract_identity_in_every_candle():
    contract = validate_contract(raw_contract(), D)
    frame = normalize_candles(sample(), contract, date(2026, 3, 23), D)
    assert frame.close.tolist() == [5, 6]
    assert frame.option_type.tolist() == ["PE", "PE"]
    assert frame.lot_size.tolist() == [65, 65]
    assert frame.instrument_key.nunique() == 1
    assert frame.timestamp.dt.tz is None
    assert frame.source_quality.eq("UPSTOX_UNVALIDATED_NEEDS_NSE_CHECK").all()


def test_mismatched_or_missing_contracts_are_rejected():
    with pytest.raises(ValueError, match="expiry"):
        validate_contract({**raw_contract(), "expiry": "2026-03-25"}, D)
    contract = validate_contract(raw_contract(), D)
    with pytest.raises(ValueError, match="exactly one"):
        find_contract([contract, contract], D, 23000, "PE")
    with pytest.raises(ValueError, match="exactly one"):
        find_contract([contract], D, 23000, "CE")


def test_invalid_ohlc_and_duplicate_candle_fails_closed():
    contract = validate_contract(raw_contract(), D)
    broken = sample()
    broken["data"]["candles"][0][3] = 9
    with pytest.raises(ValueError, match="OHLC"):
        normalize_candles(broken, contract, date(2026, 3, 23), D)
    broken = sample()
    broken["data"]["candles"].append(broken["data"]["candles"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_candles(broken, contract, date(2026, 3, 23), D)


def test_api_readonly_allowlist_and_token_not_logged():
    class Bad:
        status_code = 403
    class Fake:
        def get(self, url, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer SECRET"
            return Bad()
    broker = UpstoxClient(token="SECRET", session=Fake())
    with pytest.raises(ValueError, match="read-only"):
        broker._get("/order/place")
    with pytest.raises(RuntimeError, match="HTTP 403") as error:
        broker.expiries()
    assert "SECRET" not in str(error.value)


def test_dry_run_does_not_need_token(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    args = Namespace(action="probe", expiry=D, strike=23000.0, side="PE",
                     from_date=None, to_date=None, interval="1minute",
                     output_dir=str(tmp_path), execute=False)
    run(args)
    assert "DRY RUN" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_path_is_expiry_and_contract_scoped(tmp_path):
    c = validate_contract(raw_contract(), D)
    p = output_for(tmp_path, c, date(2026, 3, 23), D, "1minute")
    assert p.suffix == ".parquet"
    assert "2026-03-24" in p.parts
    assert "23000PE" in str(p)
