"""Yahoo Finance ticker search.

Wraps the (unofficial) ``query1.finance.yahoo.com/v1/finance/search``
endpoint. Used by the What-If Trade page so the operator can find a
ticker by company name without leaving the dashboard. Returns a list
of normalized dicts with ``symbol`` / ``name`` / ``exchange`` / ``type``.

Failure modes are absorbed: a network error, rate-limit, or schema change
returns an empty list so the calling UI can degrade gracefully ("no
matches"). The endpoint is unofficial and not covered by Yahoo's terms;
this is fine for a personal dashboard but should not be used at scale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

LOG = logging.getLogger(__name__)

YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
DEFAULT_TIMEOUT = 6  # seconds
DEFAULT_LIMIT = 8


@dataclass(frozen=True)
class TickerHit:
    """One row of a Yahoo search response."""

    symbol: str
    name: str
    exchange: str
    type: str

    def display(self) -> str:
        """Single-line label suitable for a selectbox option."""
        return f"{self.symbol}  —  {self.name}  ({self.exchange})"


def search(
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    timeout: int = DEFAULT_TIMEOUT,
    session=None,
) -> list[TickerHit]:
    """Search Yahoo Finance for a ticker matching ``query``.

    Args:
        query: Free-text company name or partial symbol.
        limit: Cap returned hits (Yahoo's max for this endpoint is ~10).
        timeout: HTTP timeout in seconds.
        session: Optional requests.Session for testing / connection pooling.

    Returns:
        A list of ``TickerHit`` (possibly empty). Never raises — network
        failures are logged and produce ``[]``.
    """
    q = (query or "").strip()
    if not q:
        return []
    try:
        if session is None:
            import requests
            session = requests.Session()
        resp = session.get(
            YAHOO_SEARCH_URL,
            params={"q": q, "quotesCount": limit, "newsCount": 0},
            timeout=timeout,
            headers={"User-Agent": "wm-dashboard/1.0 (+https://github.com/arhart3/wm-dashboard)"},
        )
        if resp.status_code != 200:
            LOG.warning("Yahoo search HTTP %s for %r", resp.status_code, q)
            return []
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Yahoo search failed for %r: %s", q, exc)
        return []

    hits: list[TickerHit] = []
    for item in (body.get("quotes") or [])[:limit]:
        sym = item.get("symbol")
        if not sym:
            continue
        hits.append(
            TickerHit(
                symbol=str(sym),
                name=str(item.get("shortname") or item.get("longname") or sym),
                exchange=str(item.get("exchDisp") or ""),
                type=str(item.get("quoteType") or ""),
            )
        )
    return hits


def search_url(query: str, *, limit: int = DEFAULT_LIMIT) -> str:
    """URL helper for callers that want to build a direct link without a network call."""
    return (
        f"{YAHOO_SEARCH_URL}?q={quote(query)}&quotesCount={limit}&newsCount=0"
    )
