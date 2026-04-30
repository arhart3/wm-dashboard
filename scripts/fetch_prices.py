"""Cron-driven price refresh.

Reads `config/tickers.yaml`, fetches latest quotes via the Finnhub -> yfinance
-> last-known-good fallback chain, and writes:

* ``data/prices.json``  — atomic snapshot, the dashboard's primary source.
* ``data/history.parquet`` — appended row per (ticker, asof_utc), idempotent.

Exit code: 0 if every ticker resolved (live or stale), 1 if any ticker has
no live source AND no historical fallback. The non-zero exit makes the
GitHub Actions run show red so the user knows the data is incomplete.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import yaml

# Allow `python scripts/fetch_prices.py` from the project root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wm_dashboard.price_providers import (  # noqa: E402
    ProviderQuote,
    fetch_finnhub,
    fetch_yfinance,
)

LOG = logging.getLogger("fetch_prices")
DATA_DIR = ROOT / "data"
PRICES_JSON = DATA_DIR / "prices.json"
HISTORY_PARQUET = DATA_DIR / "history.parquet"
TICKERS_YAML = ROOT / "config" / "tickers.yaml"

# Finnhub free tier: 60 req/min. We sleep a small amount between calls so
# back-to-back tickers stay well under that ceiling even with retries.
FINNHUB_INTER_REQUEST_SLEEP = 1.1


@dataclass(frozen=True)
class TickerSpec:
    ticker: str
    asset_class: str
    sector: str | None
    benchmark: bool


def load_tickers(path: Path = TICKERS_YAML) -> list[TickerSpec]:
    if not path.exists():
        raise FileNotFoundError(f"tickers config missing: {path}")
    with path.open() as fh:
        raw = yaml.safe_load(fh) or {}
    out: list[TickerSpec] = []
    for row in raw.get("tickers") or []:
        out.append(
            TickerSpec(
                ticker=str(row["ticker"]).upper(),
                asset_class=str(row.get("asset_class", "equity")),
                sector=row.get("sector"),
                benchmark=bool(row.get("benchmark", False)),
            )
        )
    if not out:
        raise ValueError(f"No tickers found in {path}")
    return out


def load_existing_history(path: Path = HISTORY_PARQUET) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=["asof_utc", "ticker", "price", "source", "currency", "change_pct"]
        )
    try:
        return pd.read_parquet(path)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Could not read existing history (%s); starting fresh.", exc)
        return pd.DataFrame(
            columns=["asof_utc", "ticker", "price", "source", "currency", "change_pct"]
        )


def last_known_good(history: pd.DataFrame, ticker: str) -> ProviderQuote | None:
    if history.empty:
        return None
    rows = history[history["ticker"] == ticker]
    if rows.empty:
        return None
    last = rows.sort_values("asof_utc").iloc[-1]
    return ProviderQuote(
        price=float(last["price"]),
        asof_utc=str(last["asof_utc"]),
        source="stale",
        currency=str(last.get("currency") or "USD"),
        change_pct=(
            float(last["change_pct"]) if pd.notna(last.get("change_pct")) else None
        ),
    )


def resolve_quote(
    spec: TickerSpec,
    *,
    history: pd.DataFrame,
    finnhub_key: str | None,
) -> ProviderQuote | None:
    """Walk the Finnhub -> yfinance -> stale fallback chain.

    Indices and other Finnhub-unsupported symbols are routed straight to
    yfinance to save a guaranteed-failed Finnhub call.
    """
    finnhub_supported = spec.asset_class != "index" and not spec.ticker.startswith("^")
    if finnhub_supported:
        quote = fetch_finnhub(spec.ticker, api_key=finnhub_key)
        if quote is not None:
            LOG.info("%s: finnhub %.4f", spec.ticker, quote.price)
            return quote
    quote = fetch_yfinance(spec.ticker)
    if quote is not None:
        LOG.info("%s: yfinance %.4f", spec.ticker, quote.price)
        return quote
    quote = last_known_good(history, spec.ticker)
    if quote is not None:
        LOG.warning("%s: STALE (last known good %s)", spec.ticker, quote.asof_utc)
        return quote
    LOG.error("%s: NO DATA — no live source and no historical fallback.", spec.ticker)
    return None


def atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON to ``path`` via tmp + rename. Crash-safe; never partial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".json.tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append_history(
    history: pd.DataFrame, new_rows: list[dict], path: Path = HISTORY_PARQUET
) -> pd.DataFrame:
    """Append new rows and dedupe by (asof_utc, ticker). Idempotent."""
    if not new_rows:
        return history
    additions = pd.DataFrame(new_rows)
    combined = pd.concat([history, additions], ignore_index=True)
    combined = combined.drop_duplicates(subset=["asof_utc", "ticker"], keep="last")
    combined = combined.sort_values(["asof_utc", "ticker"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".parquet.tmp", dir=path.parent)
    os.close(fd)
    try:
        combined.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return combined


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", type=Path, default=TICKERS_YAML)
    parser.add_argument("--prices", type=Path, default=PRICES_JSON)
    parser.add_argument("--history", type=Path, default=HISTORY_PARQUET)
    parser.add_argument(
        "--finnhub-sleep",
        type=float,
        default=FINNHUB_INTER_REQUEST_SLEEP,
        help="Seconds to sleep between Finnhub calls (rate-limit cushion).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    specs = load_tickers(args.tickers)
    history = load_existing_history(args.history)
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    LOG.info(
        "Refreshing %d tickers (Finnhub key %s, history rows %d)",
        len(specs),
        "set" if finnhub_key else "missing",
        len(history),
    )

    snapshot: dict[str, dict] = {}
    new_rows: list[dict] = []
    failed: list[str] = []

    for spec in specs:
        quote = resolve_quote(spec, history=history, finnhub_key=finnhub_key)
        if quote is None:
            failed.append(spec.ticker)
            continue
        snapshot[spec.ticker] = quote.to_dict()
        new_rows.append({"ticker": spec.ticker, **quote.to_dict()})
        if quote.source == "finnhub":
            time.sleep(args.finnhub_sleep)

    payload = {
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticker_count": len(snapshot),
        "failed": failed,
        "prices": snapshot,
    }
    atomic_write_json(args.prices, payload)
    append_history(history, new_rows, args.history)
    LOG.info(
        "Wrote %s (%d tickers) and %s (+%d rows)",
        args.prices,
        len(snapshot),
        args.history,
        len(new_rows),
    )
    if failed:
        LOG.error("Failed tickers (no source produced data): %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
