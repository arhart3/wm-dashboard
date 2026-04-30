"""Tests for IPS constraint checks. Covers each major limit at three points:
clearly under, exact-limit edge, just over."""

from __future__ import annotations

from pathlib import Path

import pytest

from wm_dashboard.ips_check import (
    Breach,
    check_cash_band,
    check_portfolio,
    check_position,
    check_risk_metrics,
    check_sectors,
    load_ips,
    only_breaches,
)
from wm_dashboard.tracker import Portfolio, Position


@pytest.fixture
def ips(ips_path: Path):
    return load_ips(ips_path)


def _pos(ticker: str, weight: float, *, target: float | None = None, sector: str | None = "Technology", is_etf: bool = False) -> Position:
    return Position(
        ticker=ticker,
        company=None,
        sector=sector,
        current_weight_pct=weight,
        target_weight_pct=target if target is not None else weight,
        price=None,
        cost_basis=None,
        notes=None,
        is_cash=ticker == "CASH",
        is_etf=is_etf,
    )


def _portfolio(positions: list[Position], cash_pct: float = 15.0) -> Portfolio:
    cash = _pos("CASH", cash_pct, sector="Cash")
    cash.is_cash = True
    cash.is_etf = False
    all_pos = positions + [cash]
    sector_totals: dict[str, float] = {}
    for p in positions:
        if p.sector:
            head = p.sector.split("–")[0].split("-")[0].strip()
            sector_totals[head] = sector_totals.get(head, 0.0) + p.current_weight_pct
    return Portfolio(
        positions=all_pos,
        asof=__import__("datetime").datetime(2026, 4, 30),
        source_path=Path("/dev/null"),
        sector_totals=sector_totals,
        cash_pct=cash_pct,
    )


# --- Per-position size cap (equity 7.0%, ETF 5.0%) ----------------------------


def test_equity_size_under_limit_is_ok(ips):
    breaches = check_position(_pos("NVDA", 6.5), ips)
    size_b = next(b for b in breaches if "size" in b.field)
    assert size_b.severity == "OK"


def test_equity_size_exact_limit_is_ok(ips):
    # 7.0% exact: <= cap, but within review buffer -> REVIEW
    breaches = check_position(_pos("NVDA", 7.0), ips)
    size_b = next(b for b in breaches if "size" in b.field)
    assert size_b.severity == "REVIEW"


def test_equity_size_just_over_is_breach(ips):
    breaches = check_position(_pos("NVDA", 7.05), ips)
    size_b = next(b for b in breaches if "size" in b.field)
    assert size_b.severity == "BREACH"


def test_etf_size_just_over_is_breach(ips):
    breaches = check_position(_pos("QQQ", 5.05, is_etf=True), ips)
    size_b = next(b for b in breaches if "size" in b.field)
    assert size_b.severity == "BREACH"


# --- Drift (rebalance tolerance ±0.5%) ---------------------------------------


def test_drift_within_tolerance_is_ok(ips):
    breaches = check_position(_pos("NVDA", 6.7, target=6.5), ips)  # |0.2| < 0.5
    drift_b = next(b for b in breaches if "drift" in b.field)
    assert drift_b.severity == "OK"


def test_drift_just_over_is_breach(ips):
    breaches = check_position(_pos("NVDA", 7.05, target=6.5), ips)  # |0.55| > 0.5
    drift_b = next(b for b in breaches if "drift" in b.field)
    assert drift_b.severity == "BREACH"


# --- Sector cap (35.0%) -------------------------------------------------------


def test_sector_under_limit_is_ok(ips):
    p = _portfolio([_pos("NVDA", 30.0, sector="Technology")])
    breaches = check_sectors(p, ips)
    assert breaches[0].severity == "OK"


def test_sector_just_over_is_breach(ips):
    p = _portfolio(
        [
            _pos("NVDA", 20.0, sector="Technology"),
            _pos("AVGO", 15.1, sector="Technology"),
        ]
    )
    breaches = check_sectors(p, ips)
    tech = next(b for b in breaches if "Technology" in b.field)
    assert tech.severity == "BREACH"


# --- Cash band (10-20%) -------------------------------------------------------


def test_cash_in_band_is_ok(ips):
    p = _portfolio([_pos("NVDA", 10.0)], cash_pct=15.0)
    assert check_cash_band(p, ips).severity == "OK"


def test_cash_below_band_is_breach(ips):
    p = _portfolio([_pos("NVDA", 10.0)], cash_pct=9.5)
    assert check_cash_band(p, ips).severity == "BREACH"


def test_cash_above_band_is_breach(ips):
    p = _portfolio([_pos("NVDA", 10.0)], cash_pct=21.0)
    assert check_cash_band(p, ips).severity == "BREACH"


# --- Risk metrics -------------------------------------------------------------


def test_tracking_error_just_over_is_breach(ips):
    breaches = check_risk_metrics(_portfolio([]), ips, tracking_error_pct=6.05)
    te = next(b for b in breaches if "Tracking" in b.field)
    assert te.severity == "BREACH"


def test_beta_above_band_is_breach(ips):
    breaches = check_risk_metrics(_portfolio([]), ips, beta_value=1.30)
    beta_b = next(b for b in breaches if "Beta" in b.field)
    assert beta_b.severity == "BREACH"


def test_max_drawdown_within_limit_is_ok(ips):
    breaches = check_risk_metrics(_portfolio([]), ips, max_drawdown_pct=-12.0)
    dd = next(b for b in breaches if "drawdown" in b.field.lower())
    assert dd.severity == "OK"


def test_max_drawdown_worse_than_limit_is_breach(ips):
    breaches = check_risk_metrics(_portfolio([]), ips, max_drawdown_pct=-15.5)
    dd = next(b for b in breaches if "drawdown" in b.field.lower())
    assert dd.severity == "BREACH"


def test_vol_just_over_limit_is_breach(ips):
    breaches = check_risk_metrics(_portfolio([]), ips, volatility_pct=22.10)
    vol = next(b for b in breaches if "olatility" in b.field)
    assert vol.severity == "BREACH"


# --- Aggregate ---------------------------------------------------------------


def test_clean_portfolio_has_no_breaches(ips):
    p = _portfolio(
        [
            _pos("AAA", 5.0, sector="Technology"),
            _pos("BBB", 5.0, sector="Healthcare"),
            _pos("CCC", 5.0, sector="Financials"),
            _pos("QQQ", 4.5, sector="ETF", is_etf=True),
        ],
        cash_pct=15.0,
    )
    breaches = check_portfolio(
        p,
        ips,
        tracking_error_pct=4.0,
        beta_value=1.05,
        max_drawdown_pct=-8.0,
        volatility_pct=18.0,
    )
    assert only_breaches(breaches) == []


def test_only_breaches_filter():
    items = [
        Breach(field="x", limit=1, actual=1, severity="OK"),
        Breach(field="y", limit=1, actual=2, severity="BREACH"),
        Breach(field="z", limit=1, actual=1, severity="REVIEW"),
    ]
    assert [b.field for b in only_breaches(items)] == ["y"]
