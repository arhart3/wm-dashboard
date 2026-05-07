"""Tests for the new Polygon + AlphaVantage providers + the chained
fallback order in fetch_prices.resolve_quote."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_prices  # noqa: E402

from wm_dashboard.price_providers import (  # noqa: E402
    ProviderQuote,
    fetch_alphavantage,
    fetch_polygon,
)


def _fake_response(json_body: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.text = "fake"
    return resp


def test_polygon_returns_none_without_key():
    assert fetch_polygon("AAPL", api_key=None) is None


def test_polygon_skips_index_tickers():
    assert fetch_polygon("^GSPC", api_key="x") is None


def test_polygon_parses_prev_close_response():
    session = MagicMock()
    session.get.return_value = _fake_response(
        {"results": [{"c": 175.50, "o": 173.00, "t": 1714521600000}]}
    )
    q = fetch_polygon("AAPL", api_key="x", session=session)
    assert q is not None
    assert q.source == "polygon"
    assert q.price == 175.50
    assert q.change_pct is not None and q.change_pct > 0


def test_polygon_returns_none_on_empty_results():
    session = MagicMock()
    session.get.return_value = _fake_response({"results": []})
    assert fetch_polygon("AAPL", api_key="x", session=session) is None


def test_polygon_returns_none_on_http_error():
    session = MagicMock()
    session.get.return_value = _fake_response({}, status=429)
    assert fetch_polygon("AAPL", api_key="x", session=session) is None


def test_alphavantage_parses_global_quote():
    session = MagicMock()
    session.get.return_value = _fake_response(
        {"Global Quote": {"05. price": "175.50", "08. previous close": "173.00"}}
    )
    q = fetch_alphavantage("AAPL", api_key="x", session=session)
    assert q is not None
    assert q.source == "alphavantage"
    assert q.price == 175.50


def test_alphavantage_returns_none_on_rate_limit_response():
    """Alpha Vantage returns 200 OK with a 'Note' field on rate-limit;
    treat as a miss so the fallback chain advances."""
    session = MagicMock()
    session.get.return_value = _fake_response(
        {"Note": "Thank you for using Alpha Vantage. Our standard API call frequency is..."}
    )
    assert fetch_alphavantage("AAPL", api_key="x", session=session) is None


# --- resolve_quote chain ordering --------------------------------------------


def _spec(ticker: str = "AAA", asset_class: str = "equity"):
    return fetch_prices.TickerSpec(
        ticker=ticker, asset_class=asset_class, sector=None, benchmark=False
    )


def _quote(price: float, source: str) -> ProviderQuote:
    return ProviderQuote(
        price=price,
        asof_utc="2026-05-07T13:30:00Z",
        source=source,
        currency="USD",
        change_pct=0.0,
    )


def test_resolve_uses_polygon_first_when_key_set(monkeypatch):
    monkeypatch.setattr(fetch_prices, "fetch_polygon", lambda t, api_key=None: _quote(100, "polygon"))
    monkeypatch.setattr(fetch_prices, "fetch_alphavantage", lambda t, api_key=None: _quote(101, "alphavantage"))
    monkeypatch.setattr(fetch_prices, "fetch_finnhub", lambda t, api_key=None: _quote(102, "finnhub"))
    monkeypatch.setattr(fetch_prices, "fetch_yfinance", lambda t: _quote(103, "yfinance"))
    q = fetch_prices.resolve_quote(
        _spec(),
        history=pd.DataFrame(),
        polygon_key="P",
        alphavantage_key="A",
        finnhub_key="F",
    )
    assert q is not None and q.source == "polygon"


def test_resolve_falls_through_to_alphavantage_when_polygon_misses(monkeypatch):
    monkeypatch.setattr(fetch_prices, "fetch_polygon", lambda t, api_key=None: None)
    monkeypatch.setattr(fetch_prices, "fetch_alphavantage", lambda t, api_key=None: _quote(101, "alphavantage"))
    monkeypatch.setattr(fetch_prices, "fetch_finnhub", lambda t, api_key=None: _quote(102, "finnhub"))
    monkeypatch.setattr(fetch_prices, "fetch_yfinance", lambda t: _quote(103, "yfinance"))
    q = fetch_prices.resolve_quote(
        _spec(),
        history=pd.DataFrame(),
        polygon_key="P",
        alphavantage_key="A",
        finnhub_key="F",
    )
    assert q is not None and q.source == "alphavantage"


def test_resolve_skips_paid_providers_for_index(monkeypatch):
    """^-prefixed tickers go straight to yfinance (free indices)."""
    polygon_mock = MagicMock()
    av_mock = MagicMock()
    monkeypatch.setattr(fetch_prices, "fetch_polygon", polygon_mock)
    monkeypatch.setattr(fetch_prices, "fetch_alphavantage", av_mock)
    monkeypatch.setattr(fetch_prices, "fetch_finnhub", MagicMock())
    monkeypatch.setattr(fetch_prices, "fetch_yfinance", lambda t: _quote(7000, "yfinance"))
    q = fetch_prices.resolve_quote(
        _spec(ticker="^GSPC", asset_class="index"),
        history=pd.DataFrame(),
        polygon_key="P",
        alphavantage_key="A",
        finnhub_key="F",
    )
    assert q is not None and q.source == "yfinance"
    assert not polygon_mock.called
    assert not av_mock.called


def test_resolve_uses_yfinance_when_no_paid_keys(monkeypatch):
    monkeypatch.setattr(fetch_prices, "fetch_polygon", lambda t, api_key=None: _quote(100, "polygon"))
    monkeypatch.setattr(fetch_prices, "fetch_alphavantage", lambda t, api_key=None: _quote(101, "alphavantage"))
    monkeypatch.setattr(fetch_prices, "fetch_finnhub", lambda t, api_key=None: _quote(102, "finnhub"))
    monkeypatch.setattr(fetch_prices, "fetch_yfinance", lambda t: _quote(103, "yfinance"))
    q = fetch_prices.resolve_quote(_spec(), history=pd.DataFrame())  # no keys passed
    assert q is not None and q.source == "yfinance"
