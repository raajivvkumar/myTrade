"""No-network Upstox Gamma-only terminal smoke-test tests."""
from argparse import Namespace

import pandas as pd
import pytest

from app.broker import upstox_chain_cli as cli


def raw_chain():
    market = {"ltp": 100, "volume": 100000, "oi": 250000,
              "prev_oi": 249000, "bid_price": 99.50,
              "ask_price": 100.50, "bid_qty": 50, "ask_qty": 50}
    greek = {"delta": 0.4, "gamma": 0.004, "theta": -3.0,
             "iv": 18.0, "vega": 4.0}
    return {"status": "success", "data": [{
        "expiry": "2026-10-13", "pcr": 1.0,
        "strike_price": 25000.0,
        "underlying_spot_price": 25003.0,
        "underlying_key": "NSE_INDEX|Nifty 50",
        "call_options": {
            "instrument_key": "NSE_FO|ABC123",
            "market_data": market, "option_greeks": greek,
        },
    }]}


def test_dry_run_does_not_call_broker(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "get_option_chain",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("called broker")),
    )
    result = cli.run(Namespace(expiry="current_week", top=6, execute=False))
    assert result["status"] == "DRY_RUN"
    assert "DRY RUN" in capsys.readouterr().out


def test_one_readonly_call_and_no_archiving(monkeypatch, tmp_path, capsys):
    requested = []
    def get_chain(expiry):
        requested.append(expiry)
        return raw_chain()
    monkeypatch.setattr(cli, "get_option_chain", get_chain)
    monkeypatch.chdir(tmp_path)
    result = cli.run(Namespace(expiry="current_week", top=6, execute=True))
    assert requested == ["current_week"]
    assert result["status"] == "RESEARCH_ONLY"
    assert result["option_rows"] == 1
    assert len(list(tmp_path.iterdir())) == 0
    output = capsys.readouterr().out
    assert "NOT BUY/SELL SIGNAL" in output
    assert "2026-10-13" in output
    assert "Gamma rank is RELATIVE" in output


def test_invalid_top_rejected_before_any_broker_request(monkeypatch):
    monkeypatch.setattr(cli, "get_option_chain", lambda **kwargs: (_ for _ in ()).throw(AssertionError("API request")))
    with pytest.raises(ValueError, match="between"):
        cli.run(Namespace(expiry="current_week", top=0, execute=True))


def test_empty_chain_does_not_pretend_to_find_gamma(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_option_chain", lambda **kwargs: {"status": "success", "data": []})
    result = cli.run(Namespace(expiry="current_week", top=5, execute=True))
    assert result["status"] == "EMPTY"
    assert "No option rows" in capsys.readouterr().out
