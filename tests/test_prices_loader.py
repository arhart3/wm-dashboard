"""Tests for the repo-snapshot loader in ``wm_dashboard.prices``."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from wm_dashboard.prices import (
    REPO_SNAPSHOT_FRESH,
    latest_prices,
    load_from_repo,
    repo_snapshot_age,
)


def _write_snapshot(path: Path, *, generated: datetime, prices: dict) -> None:
    payload = {
        "generated_utc": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticker_count": len(prices),
        "failed": [],
        "prices": prices,
    }
    path.write_text(json.dumps(payload))


def test_load_from_repo_returns_none_when_missing(tmp_path: Path):
    assert load_from_repo(tmp_path / "nope.json") is None


def test_load_from_repo_marks_fresh_quote_sourced(tmp_path: Path):
    snap = tmp_path / "prices.json"
    _write_snapshot(
        snap,
        generated=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5),
        prices={
            "AAA": {
                "price": 100.0,
                "asof_utc": "2026-04-30T13:30:00Z",
                "source": "finnhub",
                "currency": "USD",
                "change_pct": 0.5,
            }
        },
    )
    out = load_from_repo(snap)
    assert out is not None
    assert "AAA" in out
    assert out["AAA"].provenance == "SOURCED"
    assert out["AAA"].source == "finnhub"
    assert out["AAA"].price == 100.0


def test_load_from_repo_marks_old_snapshot_stale(tmp_path: Path):
    snap = tmp_path / "prices.json"
    _write_snapshot(
        snap,
        generated=datetime.now(UTC).replace(tzinfo=None) - REPO_SNAPSHOT_FRESH - timedelta(minutes=5),
        prices={
            "AAA": {
                "price": 100.0,
                "asof_utc": "2026-04-30T10:00:00Z",
                "source": "yfinance",
                "currency": "USD",
                "change_pct": 0.0,
            }
        },
    )
    out = load_from_repo(snap)
    assert out["AAA"].provenance == "STALE"


def test_load_from_repo_propagates_stale_source(tmp_path: Path):
    snap = tmp_path / "prices.json"
    _write_snapshot(
        snap,
        generated=datetime.now(UTC).replace(tzinfo=None),  # snapshot itself is fresh
        prices={
            "AAA": {
                "price": 100.0,
                "asof_utc": "2026-04-29T13:30:00Z",
                "source": "stale",  # provider chain failed; using LKG
                "currency": "USD",
                "change_pct": None,
            }
        },
    )
    out = load_from_repo(snap)
    # Even though the snapshot is fresh, the underlying upstream is "stale".
    assert out["AAA"].provenance == "STALE"
    assert out["AAA"].source == "stale"


def test_repo_snapshot_age_returns_generated_and_delta(tmp_path: Path):
    snap = tmp_path / "prices.json"
    gen = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
    _write_snapshot(snap, generated=gen, prices={})
    result = repo_snapshot_age(snap)
    assert result is not None
    generated, age = result
    assert abs((generated - gen).total_seconds()) < 2
    assert timedelta(minutes=9) < age < timedelta(minutes=11)


def test_latest_prices_prefers_repo_snapshot_over_live(tmp_path: Path):
    snap = tmp_path / "prices.json"
    _write_snapshot(
        snap,
        generated=datetime.now(UTC).replace(tzinfo=None),
        prices={
            "AAA": {
                "price": 999.0,  # Distinct from any live value.
                "asof_utc": "2026-04-30T13:30:00Z",
                "source": "finnhub",
                "currency": "USD",
                "change_pct": 0.0,
            }
        },
    )
    cache_dir = tmp_path / "cache"
    out = latest_prices(["AAA"], cache_dir=cache_dir, repo_snapshot_path=snap)
    assert out["AAA"].price == 999.0
    assert out["AAA"].source == "finnhub"


def test_latest_prices_skips_repo_when_path_missing(tmp_path: Path, monkeypatch):
    """When no snapshot exists and yfinance is unavailable, returns STALE without
    attempting to read a missing file (no crash)."""
    cache_dir = tmp_path / "cache"
    # Force-disable the live path by monkey-patching the internal fetcher.
    from wm_dashboard import prices

    monkeypatch.setattr(prices, "_fetch_one_latest", lambda t: None)
    out = latest_prices(["AAA"], cache_dir=cache_dir, repo_snapshot_path=tmp_path / "missing.json")
    assert out["AAA"].provenance == "STALE"
    assert out["AAA"].source == "stale"
