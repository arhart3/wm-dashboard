"""Tests for the cron-driven price refresh."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

# scripts/ isn't importable by default; load fetch_prices via its file path.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_prices  # noqa: E402

from wm_dashboard.price_providers import ProviderQuote  # noqa: E402


@pytest.fixture
def tmp_state(tmp_path: Path) -> tuple[Path, Path, Path]:
    tickers = tmp_path / "tickers.yaml"
    tickers.write_text(
        "tickers:\n"
        "  - ticker: AAA\n"
        "    asset_class: equity\n"
        "  - ticker: BBB\n"
        "    asset_class: equity\n"
        "  - ticker: '^GSPC'\n"
        "    asset_class: index\n"
        "    benchmark: true\n"
    )
    prices = tmp_path / "prices.json"
    history = tmp_path / "history.parquet"
    return tickers, prices, history


def _quote(price: float, source: str = "finnhub") -> ProviderQuote:
    return ProviderQuote(
        price=price,
        asof_utc="2026-04-30T13:45:00Z",
        source=source,
        currency="USD",
        change_pct=0.5,
    )


def test_fallback_chain_finnhub_first(tmp_state, monkeypatch):
    tickers, prices, history = tmp_state
    monkeypatch.setenv("FINNHUB_API_KEY", "fake")
    with (
        patch.object(fetch_prices, "fetch_finnhub", return_value=_quote(100.0, "finnhub")) as fh,
        patch.object(fetch_prices, "fetch_yfinance", return_value=_quote(101.0, "yfinance")) as yf,
    ):
        rc = fetch_prices.main(
            ["--tickers", str(tickers), "--prices", str(prices), "--history", str(history), "--finnhub-sleep", "0"]
        )
    assert rc == 0
    # Finnhub used for AAA + BBB; ^GSPC short-circuits and uses yfinance.
    assert fh.call_count >= 2
    assert yf.call_count >= 1
    payload = json.loads(prices.read_text())
    assert set(payload["prices"].keys()) == {"AAA", "BBB", "^GSPC"}
    assert payload["prices"]["AAA"]["source"] == "finnhub"


def test_falls_through_to_yfinance_when_finnhub_misses(tmp_state):
    tickers, prices, history = tmp_state
    with (
        patch.object(fetch_prices, "fetch_finnhub", return_value=None),
        patch.object(fetch_prices, "fetch_yfinance", return_value=_quote(50.0, "yfinance")),
    ):
        rc = fetch_prices.main(
            ["--tickers", str(tickers), "--prices", str(prices), "--history", str(history), "--finnhub-sleep", "0"]
        )
    assert rc == 0
    payload = json.loads(prices.read_text())
    for sym in ("AAA", "BBB", "^GSPC"):
        assert payload["prices"][sym]["source"] == "yfinance"


def test_falls_through_to_stale_when_both_providers_fail(tmp_state):
    tickers, prices, history = tmp_state
    # Pre-seed history with a known price for AAA so stale fallback works.
    pd.DataFrame(
        [
            {
                "asof_utc": "2026-04-29T13:30:00Z",
                "ticker": "AAA",
                "price": 42.0,
                "source": "yfinance",
                "currency": "USD",
                "change_pct": 0.0,
            }
        ]
    ).to_parquet(history, index=False)
    with (
        patch.object(fetch_prices, "fetch_finnhub", return_value=None),
        patch.object(fetch_prices, "fetch_yfinance", return_value=None),
    ):
        rc = fetch_prices.main(
            ["--tickers", str(tickers), "--prices", str(prices), "--history", str(history), "--finnhub-sleep", "0"]
        )
    assert rc == 1  # BBB and ^GSPC have no fallback.
    payload = json.loads(prices.read_text())
    assert payload["prices"]["AAA"]["source"] == "stale"
    assert payload["prices"]["AAA"]["price"] == 42.0
    assert "BBB" in payload["failed"]
    assert "^GSPC" in payload["failed"]


def test_atomic_write_no_partial_file_on_error(tmp_state):
    tickers, prices, _ = tmp_state
    # Inject a JSON-serialization error mid-write.
    bad_payload = {"x": object()}  # not JSON-serializable
    with pytest.raises(TypeError):
        fetch_prices.atomic_write_json(prices, bad_payload)
    # No final file, no .tmp leftover.
    assert not prices.exists()
    leftovers = [p for p in prices.parent.glob("prices.*.json.tmp")]
    assert leftovers == []


def test_history_append_is_idempotent(tmp_state):
    tickers, prices, history = tmp_state
    rows = [
        {
            "ticker": "AAA",
            "price": 100.0,
            "asof_utc": "2026-04-30T13:30:00Z",
            "source": "finnhub",
            "currency": "USD",
            "change_pct": 0.0,
        }
    ]
    empty = fetch_prices.load_existing_history(history)
    first = fetch_prices.append_history(empty, rows, history)
    second = fetch_prices.append_history(first, rows, history)
    third = fetch_prices.append_history(second, rows, history)
    assert len(third) == 1


def test_history_append_distinguishes_by_timestamp(tmp_state):
    _, _, history = tmp_state
    base = {
        "ticker": "AAA",
        "price": 100.0,
        "source": "finnhub",
        "currency": "USD",
        "change_pct": 0.0,
    }
    rows1 = [{**base, "asof_utc": "2026-04-30T13:30:00Z"}]
    rows2 = [{**base, "asof_utc": "2026-04-30T14:00:00Z", "price": 101.0}]
    df = fetch_prices.append_history(fetch_prices.load_existing_history(history), rows1, history)
    df = fetch_prices.append_history(df, rows2, history)
    assert len(df) == 2
    assert sorted(df["price"].tolist()) == [100.0, 101.0]


def test_finnhub_sleep_called_only_when_finnhub_used(tmp_state):
    tickers, prices, history = tmp_state
    with (
        patch.object(fetch_prices, "fetch_finnhub", return_value=None),
        patch.object(fetch_prices, "fetch_yfinance", return_value=_quote(7.0, "yfinance")),
        patch.object(fetch_prices.time, "sleep") as slept,
    ):
        fetch_prices.main(
            ["--tickers", str(tickers), "--prices", str(prices), "--history", str(history), "--finnhub-sleep", "1"]
        )
    assert slept.call_count == 0  # No finnhub hits -> no sleeps.
