"""Cloud-path: when the xlsx is unavailable, the tracker falls back to the
committed ``config/positions.yaml`` snapshot."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from wm_dashboard.tracker import load_portfolio


def test_falls_back_to_yaml_when_workbook_missing(positions_yaml_path: Path):
    portfolio = load_portfolio(
        Path("/definitely-not-a-real-workbook.xlsx"),
        positions_yaml=positions_yaml_path,
    )
    assert len(portfolio.positions) > 0
    assert isinstance(portfolio.asof, datetime)
    assert abs(portfolio.total_weight_pct - 100.0) < 0.01
    assert portfolio.cash_pct >= 10.0
    assert portfolio.cash_pct <= 25.0
    assert "Technology" in portfolio.sector_totals


def test_falls_back_handles_yaml_datetime_or_string(tmp_path: Path):
    """PyYAML auto-parses ISO timestamps to ``datetime``; loader must accept both."""
    snippet = """
asof_iso: 2026-04-30T08:00:00
positions:
  - ticker: AAA
    sector: Technology
    current_weight_pct: 5.0
    target_weight_pct: 5.0
    is_cash: false
    is_etf: false
  - ticker: CASH
    sector: Cash
    current_weight_pct: 95.0
    is_cash: true
    is_etf: false
"""
    yaml_file = tmp_path / "positions.yaml"
    yaml_file.write_text(snippet)
    portfolio = load_portfolio(
        Path("/no-workbook.xlsx"),
        positions_yaml=yaml_file,
    )
    assert portfolio.asof == datetime(2026, 4, 30, 8, 0, 0)
    assert portfolio.cash_pct == 95.0
