"""Tests for the what-if trade simulator."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from wm_dashboard.ips_check import load_ips, only_breaches
from wm_dashboard.tracker import Portfolio, Position
from wm_dashboard.whatif import Trade, simulate_trade


def _build_portfolio(cash_pct: float = 20.0) -> Portfolio:
    """Mini portfolio: UNH 3.0% + LLY 4.0% + ABBV 1.5% (Healthcare 8.5%) + cash."""
    positions = [
        Position("UNH", None, "Healthcare", 3.0, 3.0, 510.0, None, None),
        Position("LLY", None, "Healthcare", 4.0, 4.0, 944.0, None, None),
        Position("ABBV", None, "Healthcare", 1.5, 1.5, 195.0, None, None),
        Position("NVDA", None, "Technology", 6.5, 6.5, 181.86, None, None),
        Position("CASH", None, "Cash", cash_pct, 20.0, None, None, None, is_cash=True),
    ]
    sectors = {"Healthcare": 8.5, "Technology": 6.5}
    return Portfolio(
        positions=positions,
        asof=datetime(2026, 4, 30),
        source_path=Path("/dev/null"),
        sector_totals=sectors,
        cash_pct=cash_pct,
    )


def test_unh_30_to_35_with_cash_20_leaves_no_breaches(ips_path: Path):
    """Spec scenario: UNH 3.0->3.5, cash 20->19.5, healthcare 9.0, no breaches."""
    portfolio = _build_portfolio(cash_pct=20.0)
    ips = load_ips(ips_path)
    trade = Trade(
        ticker="UNH",
        action="BUY",
        size_pct=0.5,
        pre_mortem="Wrong if Q1 misses MA guidance and forces a rerate",
    )
    result = simulate_trade(portfolio, trade, ips)

    post = result.post_portfolio
    assert post.by_ticker("UNH").current_weight_pct == 3.5
    assert post.by_ticker("CASH").current_weight_pct == 19.5
    assert post.cash_pct == 19.5
    assert post.sector_totals["Healthcare"] == 9.0
    assert only_breaches(result.breaches) == []
    assert result.pre_mortem_warning is None


def test_pre_mortem_too_short_emits_warning(ips_path: Path):
    portfolio = _build_portfolio()
    ips = load_ips(ips_path)
    trade = Trade(ticker="UNH", action="BUY", size_pct=0.5, pre_mortem="too short")
    result = simulate_trade(portfolio, trade, ips)
    assert result.pre_mortem_warning is not None
    assert "Pre-mortem too short" in result.pre_mortem_warning


def test_buy_exceeds_cash_raises(ips_path: Path):
    portfolio = _build_portfolio(cash_pct=2.0)
    ips = load_ips(ips_path)
    trade = Trade(ticker="UNH", action="BUY", size_pct=5.0, pre_mortem="x" * 30)
    with pytest.raises(ValueError, match="exceeds available cash"):
        simulate_trade(portfolio, trade, ips)


def test_sell_exceeds_position_raises(ips_path: Path):
    portfolio = _build_portfolio()
    ips = load_ips(ips_path)
    trade = Trade(ticker="UNH", action="SELL", size_pct=10.0, pre_mortem="x" * 30)
    with pytest.raises(ValueError, match="exceeds"):
        simulate_trade(portfolio, trade, ips)


def test_new_ticker_requires_sector(ips_path: Path):
    portfolio = _build_portfolio()
    ips = load_ips(ips_path)
    trade = Trade(ticker="ZZZZ", action="BUY", size_pct=1.0, pre_mortem="x" * 30)
    with pytest.raises(ValueError, match="sector"):
        simulate_trade(portfolio, trade, ips)


def test_new_ticker_with_sector_is_added(ips_path: Path):
    portfolio = _build_portfolio()
    ips = load_ips(ips_path)
    trade = Trade(
        ticker="SPOT",
        action="BUY",
        size_pct=1.5,
        sector="Communication Services",
        pre_mortem="Wrong if Q1 ad-tier ARPU misses by >5% and management cuts FY guide",
    )
    result = simulate_trade(portfolio, trade, ips)
    spot = result.post_portfolio.by_ticker("SPOT")
    assert spot is not None
    assert spot.current_weight_pct == 1.5
    assert result.post_portfolio.cash_pct == 18.5


def test_pushing_position_over_size_cap_breaches(ips_path: Path):
    portfolio = _build_portfolio()
    ips = load_ips(ips_path)
    # NVDA 6.5 + 1.0 = 7.5 -> over 7.0 equity cap.
    trade = Trade(
        ticker="NVDA",
        action="BUY",
        size_pct=1.0,
        pre_mortem="Wrong if Rubin announcement slips past 2026 H2",
    )
    result = simulate_trade(portfolio, trade, ips)
    breaches = only_breaches(result.breaches)
    assert any("NVDA size" in b.field for b in breaches)


def test_original_portfolio_is_not_mutated(ips_path: Path):
    portfolio = _build_portfolio()
    ips = load_ips(ips_path)
    trade = Trade(
        ticker="UNH",
        action="BUY",
        size_pct=0.5,
        pre_mortem="Wrong if Q1 misses MA guidance and forces a rerate",
    )
    simulate_trade(portfolio, trade, ips)
    assert portfolio.by_ticker("UNH").current_weight_pct == 3.0
    assert portfolio.cash_pct == 20.0
