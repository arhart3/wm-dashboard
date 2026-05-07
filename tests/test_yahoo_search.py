"""Tests for the Yahoo Finance search service.

The HTTP layer is mocked end-to-end; tests run without a network
connection. Failure modes (HTTP error, timeout, schema change) all
produce ``[]`` so callers can degrade gracefully.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from wm_dashboard.yahoo_search import TickerHit, search


def _fake_response(json_body: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.text = "fake"
    return resp


def test_empty_query_returns_empty_list_without_http():
    assert search("") == []
    assert search("   ") == []


def test_parses_yahoo_response():
    session = MagicMock()
    session.get.return_value = _fake_response(
        {
            "quotes": [
                {"symbol": "AAPL", "shortname": "Apple Inc.", "exchDisp": "NASDAQ", "quoteType": "EQUITY"},
                {"symbol": "AAP", "longname": "Advance Auto Parts, Inc.", "exchDisp": "NYSE", "quoteType": "EQUITY"},
            ]
        }
    )
    hits = search("apple", session=session)
    assert len(hits) == 2
    assert hits[0] == TickerHit(symbol="AAPL", name="Apple Inc.", exchange="NASDAQ", type="EQUITY")
    # longname is used when shortname missing
    assert hits[1].name == "Advance Auto Parts, Inc."


def test_caps_results_to_limit():
    session = MagicMock()
    session.get.return_value = _fake_response(
        {"quotes": [{"symbol": f"T{i}", "shortname": f"Ticker {i}", "exchDisp": "NYSE"} for i in range(20)]}
    )
    assert len(search("x", limit=5, session=session)) == 5


def test_skips_rows_missing_symbol():
    session = MagicMock()
    session.get.return_value = _fake_response(
        {"quotes": [{"symbol": "AAPL"}, {"longname": "no symbol"}, {"symbol": "MSFT"}]}
    )
    hits = search("x", session=session)
    assert [h.symbol for h in hits] == ["AAPL", "MSFT"]


def test_returns_empty_on_http_error():
    session = MagicMock()
    session.get.return_value = _fake_response({}, status=429)
    assert search("aapl", session=session) == []


def test_returns_empty_on_exception():
    session = MagicMock()
    session.get.side_effect = RuntimeError("network down")
    assert search("aapl", session=session) == []


def test_returns_empty_when_no_quotes_field():
    session = MagicMock()
    session.get.return_value = _fake_response({"news": [], "lists": []})  # no 'quotes' key
    assert search("aapl", session=session) == []


def test_ticker_hit_display_formats_for_selectbox():
    h = TickerHit(symbol="NVDA", name="NVIDIA Corporation", exchange="NASDAQ", type="EQUITY")
    assert "NVDA" in h.display() and "NVIDIA Corporation" in h.display() and "NASDAQ" in h.display()
