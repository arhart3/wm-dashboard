"""yfinance wrapper with on-disk parquet caching.

The IPS §7 provenance vocabulary distinguishes:

- **SOURCED** — fetched live from yfinance during this call.
- **CACHED**  — read from disk within the TTL window (default 15 min for the
  latest quote; daily history older than yesterday is cached indefinitely).
- **STALE**   — yfinance returned nothing usable; we are returning a previously
  cached value (or ``None``) and warn the UI to flag it red.

Cache layout::

    data/cache/
      latest/<TICKER>.parquet      # one-row table per ticker
      history/<TICKER>.parquet     # full daily OHLCV history

Cache writes are best-effort; failures are logged but don't propagate.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - dependency declared in pyproject
    yf = None

LOG = logging.getLogger(__name__)
Provenance = Literal["SOURCED", "CACHED", "STALE"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache"
DEFAULT_PRICES_JSON = PROJECT_ROOT / "data" / "prices.json"
LATEST_TTL = timedelta(minutes=15)
# When the repo snapshot is older than this, treat its rows as STALE so the
# dashboard renders them red. Matches the spec ("falls back if older than 60 min").
REPO_SNAPSHOT_FRESH = timedelta(minutes=60)


@dataclass(frozen=True)
class Quote:
    """A single latest-price snapshot.

    ``source`` carries the upstream provider identity ("finnhub", "yfinance",
    "stale"). ``provenance`` carries the local-cache freshness verdict
    ("SOURCED", "CACHED", "STALE"). Both are surfaced in the UI: source as
    a small badge next to each price, provenance for cache health.
    """

    ticker: str
    price: float | None
    change_pct: float | None
    currency: str
    asof: datetime
    provenance: Provenance
    source: str = "yfinance"


def _ensure_dirs(cache_dir: Path) -> None:
    (cache_dir / "latest").mkdir(parents=True, exist_ok=True)
    (cache_dir / "history").mkdir(parents=True, exist_ok=True)


def _latest_cache_path(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / "latest" / f"{ticker.upper()}.parquet"


def _history_cache_path(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / "history" / f"{ticker.upper()}.parquet"


def _read_latest_cache(path: Path) -> Quote | None:
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        row = df.iloc[0]
        return Quote(
            ticker=str(row["ticker"]),
            price=float(row["price"]) if pd.notna(row["price"]) else None,
            change_pct=float(row["change_pct"]) if pd.notna(row["change_pct"]) else None,
            currency=str(row["currency"]),
            asof=pd.to_datetime(row["asof"]).to_pydatetime(),
            provenance="CACHED",
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Failed to read latest cache %s: %s", path, exc)
        return None


def _write_latest_cache(path: Path, quote: Quote) -> None:
    try:
        df = pd.DataFrame(
            [
                {
                    "ticker": quote.ticker,
                    "price": quote.price,
                    "change_pct": quote.change_pct,
                    "currency": quote.currency,
                    "asof": quote.asof,
                }
            ]
        )
        df.to_parquet(path, index=False)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Failed to write latest cache %s: %s", path, exc)


def _fetch_one_latest(ticker: str) -> Quote | None:
    if yf is None:
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
        change_pct = (price / prev_close - 1.0) * 100.0 if prev_close else None
        currency = (t.fast_info.get("currency") if hasattr(t, "fast_info") else None) or "USD"
        asof = hist.index[-1].to_pydatetime()
        if asof.tzinfo is not None:
            asof = asof.replace(tzinfo=None)
        return Quote(
            ticker=ticker.upper(),
            price=price,
            change_pct=change_pct,
            currency=currency,
            asof=asof,
            provenance="SOURCED",
            source="yfinance",
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return None


def _parse_iso_utc(s: str) -> datetime:
    # Accept "2026-04-30T13:45:11Z" or "...+00:00"; return a tz-naive UTC stamp.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def load_from_repo(path: Path = DEFAULT_PRICES_JSON) -> dict[str, Quote] | None:
    """Read the cron-produced ``data/prices.json`` snapshot.

    Returns ``None`` if the file does not exist (callers should fall back to
    live fetch). Quotes whose snapshot is older than ``REPO_SNAPSHOT_FRESH``
    are tagged ``STALE``; otherwise ``SOURCED`` (with ``source`` carrying the
    upstream provider name from the snapshot).
    """
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            payload = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Could not read repo snapshot %s: %s", path, exc)
        return None

    generated = _parse_iso_utc(payload.get("generated_utc") or _utcnow_iso())
    age = datetime.now(UTC).replace(tzinfo=None) - generated
    overall_stale = age > REPO_SNAPSHOT_FRESH

    out: dict[str, Quote] = {}
    for ticker, rec in (payload.get("prices") or {}).items():
        try:
            asof = _parse_iso_utc(rec["asof_utc"])
        except Exception:  # noqa: BLE001
            asof = generated
        upstream = str(rec.get("source") or "yfinance").lower()
        is_stale = overall_stale or upstream == "stale"
        out[ticker.upper()] = Quote(
            ticker=ticker.upper(),
            price=float(rec["price"]) if rec.get("price") is not None else None,
            change_pct=(
                float(rec["change_pct"]) if rec.get("change_pct") is not None else None
            ),
            currency=str(rec.get("currency") or "USD"),
            asof=asof,
            provenance="STALE" if is_stale else "SOURCED",
            source=upstream,
        )
    return out


def repo_snapshot_age(path: Path = DEFAULT_PRICES_JSON) -> tuple[datetime, timedelta] | None:
    """Return ``(generated_utc, age)`` for the snapshot, or ``None`` if missing."""
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            payload = json.load(fh)
        generated = _parse_iso_utc(payload.get("generated_utc"))
        return generated, datetime.now(UTC).replace(tzinfo=None) - generated
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Could not stat repo snapshot %s: %s", path, exc)
        return None


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def latest_prices(
    tickers: list[str],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    ttl: timedelta = LATEST_TTL,
    force_refresh: bool = False,
    repo_snapshot_path: Path | None = DEFAULT_PRICES_JSON,
) -> dict[str, Quote]:
    """Return latest quotes for ``tickers``.

    Resolution order per ticker:

    1. **Repo snapshot** (``data/prices.json``) — cron-refreshed every 30 min.
       Quotes whose snapshot is < 60 min old are tagged ``SOURCED`` with the
       upstream provider name; older entries are tagged ``STALE``.
    2. **Local parquet cache** within ``ttl``.
    3. **Live yfinance fetch** — written back to the cache.
    4. Stale cached value tagged ``STALE``.
    """
    _ensure_dirs(cache_dir)
    out: dict[str, Quote] = {}
    now = datetime.now()
    repo = load_from_repo(repo_snapshot_path) if repo_snapshot_path else None

    for raw in tickers:
        ticker = raw.upper()
        if repo is not None and ticker in repo:
            out[ticker] = repo[ticker]
            continue
        path = _latest_cache_path(cache_dir, ticker)
        cached = None if force_refresh else _read_latest_cache(path)
        if cached and cached.asof and (now - cached.asof) < ttl:
            out[ticker] = cached
            continue
        fetched = _fetch_one_latest(ticker)
        if fetched is not None:
            _write_latest_cache(path, fetched)
            out[ticker] = fetched
        elif cached is not None:
            out[ticker] = Quote(
                ticker=cached.ticker,
                price=cached.price,
                change_pct=cached.change_pct,
                currency=cached.currency,
                asof=cached.asof,
                provenance="STALE",
                source=cached.source,
            )
        else:
            out[ticker] = Quote(
                ticker=ticker,
                price=None,
                change_pct=None,
                currency="USD",
                asof=now,
                provenance="STALE",
                source="stale",
            )
    return out


def history(
    tickers: list[str],
    start: datetime | str,
    end: datetime | str | None = None,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> pd.DataFrame:
    """Return a wide DataFrame of adjusted close prices indexed by date.

    Columns are ticker symbols. Caches the full history per-ticker in parquet
    and re-fetches only when the cache stops at a date earlier than ``end``.
    """
    _ensure_dirs(cache_dir)
    # Always work in tz-naive timestamps. yfinance can return tz-aware indices
    # (e.g. America/New_York) which won't compare with bare pd.Timestamp.now().
    start_ts = pd.to_datetime(start).tz_localize(None) if pd.to_datetime(start).tzinfo else pd.to_datetime(start)
    end_ts = pd.to_datetime(end) if end is not None else pd.Timestamp.now().normalize()
    if end_ts.tzinfo is not None:
        end_ts = end_ts.tz_localize(None)
    yesterday = pd.Timestamp.now().normalize() - pd.Timedelta(days=1)

    frames: dict[str, pd.Series] = {}
    for raw in tickers:
        ticker = raw.upper()
        path = _history_cache_path(cache_dir, ticker)
        cached: pd.DataFrame | None = None
        if path.exists():
            try:
                cached = pd.read_parquet(path)
                if not cached.empty:
                    cached.index = pd.to_datetime(cached.index)
                    if cached.index.tz is not None:
                        cached.index = cached.index.tz_localize(None)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Failed to read history cache %s: %s", path, exc)
                cached = None

        need_refresh = (
            cached is None
            or cached.empty
            or cached.index.max() < min(end_ts, yesterday)
            or cached.index.min() > start_ts
        )
        if need_refresh and yf is not None:
            try:
                fetched = yf.download(
                    ticker,
                    start=start_ts.strftime("%Y-%m-%d"),
                    end=(end_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                    progress=False,
                    auto_adjust=True,
                )
                if fetched is not None and not fetched.empty:
                    if isinstance(fetched.columns, pd.MultiIndex):
                        fetched.columns = fetched.columns.get_level_values(0)
                    fetched.index = pd.to_datetime(fetched.index)
                    if fetched.index.tz is not None:
                        fetched.index = fetched.index.tz_localize(None)
                    if cached is not None and not cached.empty:
                        merged = pd.concat([cached, fetched])
                        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                    else:
                        merged = fetched.sort_index()
                    try:
                        merged.to_parquet(path)
                    except Exception as exc:  # noqa: BLE001
                        LOG.warning("Failed to write history cache %s: %s", path, exc)
                    cached = merged
            except Exception as exc:  # noqa: BLE001
                LOG.warning("yfinance history fetch failed for %s: %s", ticker, exc)

        if cached is not None and not cached.empty and "Close" in cached.columns:
            series = cached["Close"].loc[
                (cached.index >= start_ts) & (cached.index <= end_ts)
            ]
            frames[ticker] = series.rename(ticker)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames.values(), axis=1).sort_index()
