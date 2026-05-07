"""Provider adapters for ``scripts/fetch_prices.py``.

Each provider exposes a single function ``fetch_one(ticker)`` returning a
``ProviderQuote`` or ``None``. Keeping the providers in one tiny module makes
it trivial to mock them out in tests and to extend the fallback chain later.

The shape of ``ProviderQuote`` matches the on-disk record we write to
``data/prices.json``::

    {
        "price":      151.23,
        "asof_utc":   "2026-04-30T13:45:11Z",
        "source":     "finnhub" | "yfinance" | "stale",
        "currency":   "USD",
        "change_pct": -0.42         # optional, may be None
    }
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

LOG = logging.getLogger(__name__)

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
POLYGON_PREV_URL = "https://api.polygon.io/v2/aggs/ticker/{ticker}/prev"
ALPHAVANTAGE_QUOTE_URL = "https://www.alphavantage.co/query"
HTTP_TIMEOUT = 8  # seconds — applies to all provider HTTP calls


@dataclass(frozen=True)
class ProviderQuote:
    """One quote tagged with its upstream source."""

    price: float
    asof_utc: str
    source: str  # "finnhub" | "yfinance" | "stale"
    currency: str = "USD"
    change_pct: float | None = None

    def to_dict(self) -> dict[str, float | str | None]:
        return asdict(self)


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_polygon(ticker: str, *, api_key: str | None = None, session=None) -> ProviderQuote | None:
    """Fetch a single quote from Polygon.io's previous-close endpoint.

    Polygon's ``/v2/aggs/ticker/{T}/prev`` returns the prior trading day's
    OHLCV. On the free tier (5 calls/min, 2-year history), it's the
    cheapest "real" market-data feed. Returns ``None`` on missing key,
    HTTP failure, or empty result.
    """
    key = api_key if api_key is not None else os.environ.get("POLYGON_API_KEY")
    if not key:
        return None
    if ticker.startswith("^"):
        # Polygon uses I:SPX format for indices, not ^GSPC. Skip; let yfinance handle.
        return None
    try:
        if session is None:
            import requests
            session = requests.Session()
        resp = session.get(
            POLYGON_PREV_URL.format(ticker=ticker),
            params={"adjusted": "true", "apiKey": key},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            LOG.warning("Polygon HTTP %s for %s: %s", resp.status_code, ticker, resp.text[:200])
            return None
        body = resp.json()
        results = body.get("results") or []
        if not results:
            return None
        bar = results[0]
        price = float(bar.get("c") or 0.0)
        prev_close = float(bar.get("o") or 0.0)  # use open as a stand-in for prior comparison
        if price <= 0:
            return None
        ts = bar.get("t")  # epoch ms
        asof = (
            datetime.fromtimestamp(int(ts) / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if ts
            else _utcnow_iso()
        )
        # change_pct is intra-bar: close vs open. Not the prior-day close-to-close,
        # but close enough for at-a-glance directional signal. yfinance handles
        # close-to-close better; we annotate the source so callers can decide.
        change_pct = ((price / prev_close - 1.0) * 100.0) if prev_close > 0 else None
        return ProviderQuote(
            price=price,
            asof_utc=asof,
            source="polygon",
            currency="USD",
            change_pct=change_pct,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Polygon fetch failed for %s: %s", ticker, exc)
        return None


def fetch_alphavantage(ticker: str, *, api_key: str | None = None, session=None) -> ProviderQuote | None:
    """Fetch a single quote from Alpha Vantage's GLOBAL_QUOTE endpoint.

    Free tier is heavily rate-limited (5 calls/min, 500/day). Useful as a
    fallback for the few tickers Polygon and Finnhub miss.
    """
    key = api_key if api_key is not None else os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        return None
    if ticker.startswith("^"):
        return None
    try:
        if session is None:
            import requests
            session = requests.Session()
        resp = session.get(
            ALPHAVANTAGE_QUOTE_URL,
            params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": key},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            LOG.warning("AlphaVantage HTTP %s for %s", resp.status_code, ticker)
            return None
        body = resp.json()
        quote = body.get("Global Quote") or {}
        price = float(quote.get("05. price") or 0.0)
        prev_close = float(quote.get("08. previous close") or 0.0)
        if price <= 0:
            # Alpha Vantage rate-limit responses come back as 200 with a "Note" field;
            # treat them as a miss so the fallback chain advances.
            return None
        change_pct = ((price / prev_close - 1.0) * 100.0) if prev_close > 0 else None
        return ProviderQuote(
            price=price,
            asof_utc=_utcnow_iso(),
            source="alphavantage",
            currency="USD",
            change_pct=change_pct,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("AlphaVantage fetch failed for %s: %s", ticker, exc)
        return None


def fetch_finnhub(ticker: str, *, api_key: str | None = None, session=None) -> ProviderQuote | None:
    """Fetch a single quote from Finnhub.

    Returns ``None`` if no API key is set, the ticker isn't supported (e.g.
    indices like ``^GSPC`` on the free tier), or any HTTP / parse failure
    occurs. Indices are short-circuited by the leading ``^`` to save a
    request — Finnhub's free tier rejects them with HTTP 403.
    """
    key = api_key if api_key is not None else os.environ.get("FINNHUB_API_KEY")
    if not key:
        return None
    if ticker.startswith("^"):
        return None
    try:
        if session is None:
            import requests
            session = requests.Session()
        resp = session.get(
            FINNHUB_QUOTE_URL,
            params={"symbol": ticker, "token": key},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            LOG.warning("Finnhub HTTP %s for %s: %s", resp.status_code, ticker, resp.text[:200])
            return None
        body = resp.json()
        # Finnhub returns {c, h, l, o, pc, t}. ``c`` is the latest trade price.
        # An empty / unsupported symbol returns all zeros — treat as miss.
        price = float(body.get("c") or 0.0)
        prev_close = float(body.get("pc") or 0.0)
        if price <= 0:
            return None
        ts = body.get("t")
        asof = (
            datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if ts
            else _utcnow_iso()
        )
        change_pct = ((price / prev_close - 1.0) * 100.0) if prev_close > 0 else None
        return ProviderQuote(
            price=price,
            asof_utc=asof,
            source="finnhub",
            currency="USD",
            change_pct=change_pct,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Finnhub fetch failed for %s: %s", ticker, exc)
        return None


def fetch_yfinance(ticker: str) -> ProviderQuote | None:
    """Fetch a single quote from yfinance (delayed but free, no API key)."""
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        last = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else last
        price = float(last["Close"])
        prev_close = float(prev["Close"])
        if price <= 0:
            return None
        change_pct = ((price / prev_close - 1.0) * 100.0) if prev_close > 0 else None
        currency = "USD"
        try:
            currency = (t.fast_info.get("currency") if hasattr(t, "fast_info") else None) or "USD"
        except Exception:  # noqa: BLE001
            pass
        idx = hist.index[-1]
        asof_dt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        if asof_dt.tzinfo is None:
            asof_dt = asof_dt.replace(tzinfo=UTC)
        asof = asof_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ProviderQuote(
            price=price,
            asof_utc=asof,
            source="yfinance",
            currency=currency,
            change_pct=change_pct,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return None
