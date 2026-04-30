"""Risk and attribution metrics for the WM Growth Portfolio.

Implements the formulas referenced by the IPS and the institutional report
templates: time-weighted geometric chaining, Sharpe / Sortino, annualized
volatility, beta, tracking error, information ratio, max drawdown, and a
single-period Brinson-Fachler decomposition.

Conventions:
- Returns are simple (arithmetic) returns expressed as decimals (0.01 = 1%).
- Risk-free rate is annualized; daily rf = (1 + rf)^(1/periods_per_year) - 1.
- Annualization assumes ``periods_per_year`` (default 252 trading days).

These functions are pure: they accept numeric arrays / sequences and return
numeric scalars or pandas Series. They do not perform any I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def chain_twr(returns: Sequence[float]) -> float:
    """Geometrically chain a sequence of period returns into a cumulative TWR.

    Args:
        returns: Period returns (decimals).

    Returns:
        Compounded total return ``prod(1 + r_i) - 1``.

    Examples:
        >>> round(chain_twr([0.01793, 0.03020]), 5)
        0.04867
    """
    if len(returns) == 0:
        return 0.0
    arr = np.asarray(list(returns), dtype=float)
    return float(np.prod(1.0 + arr) - 1.0)


def annualize_return(total_return: float, periods: int, periods_per_year: int = TRADING_DAYS) -> float:
    """Annualize a total return observed over ``periods`` periods."""
    if periods <= 0:
        return 0.0
    return float((1.0 + total_return) ** (periods_per_year / periods) - 1.0)


def annualized_volatility(returns: Sequence[float], periods_per_year: int = TRADING_DAYS) -> float:
    """Annualized standard deviation of period returns (sample stdev, ddof=1)."""
    arr = np.asarray(list(returns), dtype=float)
    if arr.size < 2:
        return float("nan")
    return float(np.std(arr, ddof=1) * np.sqrt(periods_per_year))


def downside_volatility(
    returns: Sequence[float],
    mar: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> float:
    """Annualized downside deviation versus a minimum acceptable return (MAR)."""
    arr = np.asarray(list(returns), dtype=float)
    if arr.size < 2:
        return float("nan")
    downside = np.minimum(arr - mar, 0.0)
    return float(np.sqrt(np.mean(downside ** 2)) * np.sqrt(periods_per_year))


def sharpe_ratio(
    returns: Sequence[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> float:
    """Annualized Sharpe ratio using period excess returns."""
    arr = np.asarray(list(returns), dtype=float)
    if arr.size < 2:
        return float("nan")
    rf_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = arr - rf_period
    sd = np.std(excess, ddof=1)
    if sd == 0:
        return float("nan")
    return float(np.mean(excess) / sd * np.sqrt(periods_per_year))


def sortino_ratio(
    returns: Sequence[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> float:
    """Annualized Sortino ratio (Sharpe variant penalizing only downside)."""
    arr = np.asarray(list(returns), dtype=float)
    if arr.size < 2:
        return float("nan")
    rf_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = arr - rf_period
    dd = downside_volatility(arr.tolist(), mar=rf_period, periods_per_year=periods_per_year)
    if dd == 0 or np.isnan(dd):
        return float("nan")
    return float(np.mean(excess) * periods_per_year / dd)


def beta(portfolio_returns: Sequence[float], benchmark_returns: Sequence[float]) -> float:
    """OLS beta of portfolio vs. benchmark period returns."""
    p = np.asarray(list(portfolio_returns), dtype=float)
    b = np.asarray(list(benchmark_returns), dtype=float)
    if p.size != b.size or p.size < 2:
        return float("nan")
    var_b = np.var(b, ddof=1)
    if var_b == 0:
        return float("nan")
    cov = np.cov(p, b, ddof=1)[0, 1]
    return float(cov / var_b)


def tracking_error(
    portfolio_returns: Sequence[float],
    benchmark_returns: Sequence[float],
    periods_per_year: int = TRADING_DAYS,
) -> float:
    """Annualized tracking error: std-dev of (portfolio - benchmark) returns."""
    p = np.asarray(list(portfolio_returns), dtype=float)
    b = np.asarray(list(benchmark_returns), dtype=float)
    if p.size != b.size or p.size < 2:
        return float("nan")
    diff = p - b
    return float(np.std(diff, ddof=1) * np.sqrt(periods_per_year))


def information_ratio(
    portfolio_returns: Sequence[float],
    benchmark_returns: Sequence[float],
    periods_per_year: int = TRADING_DAYS,
) -> float:
    """Annualized information ratio: mean active return / tracking error."""
    p = np.asarray(list(portfolio_returns), dtype=float)
    b = np.asarray(list(benchmark_returns), dtype=float)
    if p.size != b.size or p.size < 2:
        return float("nan")
    diff = p - b
    sd = np.std(diff, ddof=1)
    if sd == 0:
        return float("nan")
    return float(np.mean(diff) / sd * np.sqrt(periods_per_year))


def max_drawdown(returns: Sequence[float]) -> float:
    """Maximum drawdown over the return series (negative decimal, e.g. -0.12)."""
    arr = np.asarray(list(returns), dtype=float)
    if arr.size == 0:
        return 0.0
    equity = np.cumprod(1.0 + arr)
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    return float(np.min(drawdown))


def equity_curve(returns: Sequence[float], starting_value: float = 1.0) -> pd.Series:
    """Return a cumulative equity curve from a sequence of period returns."""
    arr = np.asarray(list(returns), dtype=float)
    return pd.Series(starting_value * np.cumprod(1.0 + arr))


@dataclass(frozen=True)
class BrinsonFachlerComponent:
    """Single-sector decomposition produced by ``brinson_fachler``."""

    sector: str
    allocation: float
    selection: float
    interaction: float
    total: float


def brinson_fachler(
    portfolio_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_weights: dict[str, float],
    benchmark_returns: dict[str, float],
) -> list[BrinsonFachlerComponent]:
    """Single-period Brinson-Fachler attribution by sector.

    Args:
        portfolio_weights: Sector -> portfolio weight (decimal).
        portfolio_returns: Sector -> portfolio return for the period.
        benchmark_weights: Sector -> benchmark weight (decimal).
        benchmark_returns: Sector -> benchmark return for the period.

    Returns:
        One ``BrinsonFachlerComponent`` per sector. Allocation = (wp - wb) *
        (rb - total benchmark return); Selection = wb * (rp - rb); Interaction
        = (wp - wb) * (rp - rb).
    """
    sectors = sorted(set(portfolio_weights) | set(benchmark_weights))
    bench_total = sum(
        benchmark_weights.get(s, 0.0) * benchmark_returns.get(s, 0.0) for s in sectors
    )
    out: list[BrinsonFachlerComponent] = []
    for s in sectors:
        wp = portfolio_weights.get(s, 0.0)
        wb = benchmark_weights.get(s, 0.0)
        rp = portfolio_returns.get(s, 0.0)
        rb = benchmark_returns.get(s, 0.0)
        allocation = (wp - wb) * (rb - bench_total)
        selection = wb * (rp - rb)
        interaction = (wp - wb) * (rp - rb)
        out.append(
            BrinsonFachlerComponent(
                sector=s,
                allocation=allocation,
                selection=selection,
                interaction=interaction,
                total=allocation + selection + interaction,
            )
        )
    return out
