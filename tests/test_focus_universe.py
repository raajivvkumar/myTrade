from __future__ import annotations

from app.broker.focus_universe import FOCUS_MARKETS, focus_instruments


def test_index_focus_includes_only_requested_index_families() -> None:
    master = [
        {"exch_seg": "NSE", "symbol": "Nifty 50", "name": "Nifty 50", "token": "1", "instrumenttype": ""},
        {"exch_seg": "NFO", "symbol": "NIFTY26OCTFUT", "name": "NIFTY", "token": "2", "instrumenttype": "FUTIDX"},
        {"exch_seg": "NSE", "symbol": "NIFTY BANK", "name": "NIFTY BANK", "token": "3", "instrumenttype": ""},
        {"exch_seg": "NFO", "symbol": "BANKNIFTY26OCTFUT", "name": "BANKNIFTY", "token": "4", "instrumenttype": "FUTIDX"},
        {"exch_seg": "NSE", "symbol": "NIFTY MID SELECT", "name": "NIFTY MID SELECT", "token": "5", "instrumenttype": ""},
        {"exch_seg": "NFO", "symbol": "MIDCPNIFTY26OCTFUT", "name": "MIDCPNIFTY", "token": "6", "instrumenttype": "FUTIDX"},
        {"exch_seg": "NSE", "symbol": "NIFTY IT", "name": "NIFTY IT", "token": "7", "instrumenttype": ""},
    ]

    nifty = focus_instruments(master, "NIFTY 50")
    bank = focus_instruments(master, "BANKNIFTY")
    midcap = focus_instruments(master, "MIDCPNIFTY")

    assert {item["token"] for item in nifty} == {"1", "2"}
    assert {item["token"] for item in bank} == {"3", "4"}
    assert {item["token"] for item in midcap} == {"5", "6"}


def test_commodity_focus_uses_mcx_contracts() -> None:
    master = [
        {"exch_seg": "MCX", "symbol": "CRUDEOIL26OCTFUT", "token": "1", "instrumenttype": "FUTCOM"},
        {"exch_seg": "NFO", "symbol": "NIFTY26OCTFUT", "token": "2", "instrumenttype": "FUTIDX"},
        {"exch_seg": "MCX", "symbol": "SILVER26OCTOPT", "token": "3", "instrumenttype": "OPTFUT"},
    ]
    matches = focus_instruments(master, "MCX COMMODITIES")
    assert {item["token"] for item in matches} == {"1", "3"}


def test_unknown_focus_is_rejected() -> None:
    try:
        focus_instruments([], "SENSEX")
    except ValueError as error:
        assert "Unsupported" in str(error)
    else:
        raise AssertionError("Unknown market focus should be rejected")
