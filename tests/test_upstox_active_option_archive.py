"""Offline/free-Upstox active option archive tests: no real tokens/network."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from argparse import Namespace
import json

import pandas as pd
import pytest

from app.broker.upstox_active_option_archive import (
    ActiveOptionClient, INDEX, IST, archive_one_day, quality,
    run, target_path, validate_candles,
)
from app.data.market_archive import sha256_file

DAY = date(2026, 10, 7)
EXPIRY = "2026-10-27"


def contract():
    return {
        "instrument_key": "NSE_FO|REALKEY123",
        "underlying_key": INDEX, "underlying": "NIFTY",
        "strike_price": 25000.0, "option_type": "PE",
        "expiry": EXPIRY,
    }


def session_candles(n=375):
    t = pd.date_range("2026-10-07 09:15", periods=n, freq="min", tz=IST)
    return [[x.isoformat(), 10.0, 11.0, 9.0, 10.5, 75, 1000] for x in t]


def fake_frame(n=375):
    return validate_candles({"candles": session_candles(n)},
                            contract=contract(), trading_day=DAY)


class Response:
    status_code = 200

    def __init__(self, body, *, code=200):
        self.body = body
        self.status_code = code

    def json(self):
        return self.body


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def chain():
    return {"status": "success", "data": [{
        "expiry": EXPIRY, "underlying_key": INDEX, "strike_price": 25000.0,
        "put_options": {"instrument_key": "NSE_FO|REALKEY123"},
        "call_options": {"instrument_key": "NSE_FO|CALL0001"},
    }]}


def args(**overrides):
    data = dict(expiry="current_month", strike=25000., side="PE",
                date=DAY, execute=False, save=False,
                source_dir=None, backup_dir=None)
    data.update(overrides)
    return Namespace(**data)


def test_network_free_default_dry_run(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    assert run(args())["requests"] == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="requires"):
        run(args(save=True))


def test_free_token_two_get_calls_exact_contract_and_candle_data():
    session = Session([
        Response(chain()),
        Response({"status": "success", "data": {"candles": session_candles(2)}}),
    ])
    broker = ActiveOptionClient(token="secret-NOT-PRINTED", session=session)
    chosen = broker.current_contract(expiry="current_month", strike=25000, side="PE")
    assert chosen == contract()
    frame = broker.candles(chosen, DAY)
    assert len(frame) == 2
    assert frame.timestamp.iloc[0].isoformat() == "2026-10-07T09:15:00"
    assert frame.instrument_key.eq("NSE_FO|REALKEY123").all()
    assert frame.data_quality.eq("UPSTOX_BASIC_ACTIVE_UNVERIFIED").all()
    assert session.calls[0][0] == "https://api.upstox.com/v2/option/chain"
    assert session.calls[0][1]["params"] == {
        "instrument_key": INDEX, "expiry_date": "current_month",
    }
    assert session.calls[1][0] == (
        "https://api.upstox.com/v3/historical-candle/"
        "NSE_FO%7CREALKEY123/minutes/1/2026-10-07/2026-10-07"
    )
    assert len(session.calls) == 2
    for _, kwargs in session.calls:
        assert kwargs["headers"]["Authorization"] == "Bearer secret-NOT-PRINTED"


def test_invalid_contract_does_not_trigger_historical_fetch():
    s = Session([Response(chain())])
    b = ActiveOptionClient(token="test", session=s)
    with pytest.raises(ValueError, match="unique"):
        b.current_contract(expiry="current_month", strike=25100, side="PE")
    assert len(s.calls) == 1
    with pytest.raises(ValueError, match="expired-contract"):
        b.current_contract(expiry="2026-01-01", strike=25000, side="PE")
    assert len(s.calls) == 1


@pytest.mark.parametrize("mutator", [
    lambda r: r[0].__setitem__(2, 8.0),
    lambda r: r[0].__setitem__(5, -1),
    lambda r: r.append(r[0]),
    lambda r: r[0].__setitem__(0, "2026-10-07T09:15:30+05:30"),
    lambda r: r[0].__setitem__(0, "2026-10-08T09:15:00+05:30"),
    lambda r: r[0].__setitem__(0, "2026-10-07T09:15:00"),
])
def test_malformed_candles_rejected(mutator):
    rows = session_candles(2)
    mutator(rows)
    with pytest.raises(ValueError):
        validate_candles({"candles": rows}, contract=contract(), trading_day=DAY)


def test_quality_marks_only_contiguous_complete_375_minute_session():
    good = fake_frame()
    assert quality(good)["research_ready_full_session"] is True
    assert quality(good)["missing_regular_minutes"] == 0
    missing = good.drop(index=75).reset_index(drop=True)
    assert quality(missing)["research_ready_full_session"] is False
    assert quality(missing)["missing_regular_minutes"] == 1


def test_immutable_primary_and_backup_independently_verified(tmp_path):
    source = tmp_path / "primary"
    backup = tmp_path / "independent"
    result = archive_one_day(
        fake_frame(), contract(), DAY, source_dir=source, mirror_dir=backup,
    )
    primary_file = target_path(source, contract(), DAY)
    backup_file = backup / primary_file.relative_to(source)
    assert primary_file.exists() and backup_file.exists()
    assert sha256_file(primary_file) == sha256_file(backup_file)
    assert sha256_file(primary_file) == result["sha256"]
    metadata = json.loads(primary_file.with_suffix(".json").read_text())
    assert metadata["data_kind"] == "EXACT_CONTRACT_OPTION_CANDLES_UNVERIFIED"
    assert metadata["parquet_sha256"] == result["sha256"]
    assert metadata["quality"]["research_ready_full_session"] is True
    restored = pd.read_parquet(backup_file)
    assert len(restored) == 375
    assert restored.instrument_key.eq(contract()["instrument_key"]).all()
    assert restored["close"].iat[-1] == 10.5

    second = archive_one_day(
        fake_frame(), contract(), DAY, source_dir=source, mirror_dir=backup,
    )
    assert result["sha256"] == second["sha256"]
    modified = fake_frame()
    modified.loc[2, "close"] = 10.4
    with pytest.raises(ValueError, match="overwrite"):
        archive_one_day(
            modified, contract(), DAY, source_dir=source, mirror_dir=backup,
        )


def test_backup_tampering_rejected_without_mutating_primary(tmp_path):
    source = tmp_path / "primary"
    backup = tmp_path / "backup"
    archive_one_day(fake_frame(), contract(), DAY, source_dir=source, mirror_dir=backup)
    original = target_path(source, contract(), DAY)
    mirror = backup / original.relative_to(source)
    mirror.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="differs"):
        archive_one_day(fake_frame(), contract(), DAY, source_dir=source, mirror_dir=backup)
    assert sha256_file(original) != sha256_file(mirror)


def test_backup_must_not_be_within_source(tmp_path):
    with pytest.raises(ValueError, match="separate"):
        archive_one_day(
            fake_frame(1), contract(), DAY,
            source_dir=tmp_path / "base",
            mirror_dir=tmp_path / "base" / "backup",
        )


def test_no_save_explicit_network_does_not_create_files(tmp_path):
    class Broker:
        def current_contract(self, **kwargs):
            return contract()
        def candles(self, selected, day):
            return fake_frame(2)
    output = run(args(execute=True, source_dir=tmp_path / "src",
                      backup_dir=tmp_path / "backup"), client=Broker())
    assert output["status"] == "IN_MEMORY"
    assert not list(tmp_path.iterdir())


def test_secret_token_not_logged_when_upstox_rejects(monkeypatch):
    broker = ActiveOptionClient(token="HIDDEN", session=Session([
        Response({"error": "HIDDEN"}, code=403),
    ]))
    with pytest.raises(RuntimeError, match="HTTP 403") as err:
        broker.current_contract(expiry="current_week", strike=25000, side="PE")
    assert "HIDDEN" not in str(err.value)


def test_no_order_method():
    assert not hasattr(ActiveOptionClient, "place_order")
    assert not hasattr(ActiveOptionClient, "post")
