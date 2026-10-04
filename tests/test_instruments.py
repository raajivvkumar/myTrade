from app.broker.instruments import search_instruments


def test_search_instruments_filters_query_and_exchange() -> None:
    instruments = [
        {
            "token": "99926000",
            "symbol": "Nifty 50",
            "name": "NIFTY",
            "exch_seg": "NSE",
            "instrumenttype": "AMXIDX",
        },
        {
            "token": "2885",
            "symbol": "RELIANCE-EQ",
            "name": "RELIANCE",
            "exch_seg": "NSE",
            "instrumenttype": "",
        },
    ]

    result = search_instruments(instruments, "NIFTY", exchange="NSE")

    assert len(result) == 1
    assert result[0]["token"] == "99926000"
