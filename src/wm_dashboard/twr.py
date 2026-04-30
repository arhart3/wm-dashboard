"""Time-weighted return computations for the Performance page.

Wraps :mod:`wm_dashboard.risk_attribution` with a price-history → return-series
adapter and a small ``compute_curves`` helper that returns chartable DataFrames
suitable for Plotly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from .risk_attribution import (
    annualize_return,
    annualized_volatility,
    beta,
    chain_twr,
    information_ratio,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
    tracking_error,
)


@dataclass(frozen=True)
class PerformanceSnapshot:
    """Bundle of return and risk metrics over a single window."""

    twr: float
    twr_annualized: float
    benchmark_twr: float
    alpha: float
    volatility: float
    tracking_error: float
    beta: float
    sharpe: float
    sortino: float
    information_ratio: float
    max_drawdown: float
    n_observations: int


def daily_returns(price_series: pd.Series) -> pd.Series:
    """Daily simple returns from a price series; first value dropped."""
    return price_series.pct_change().dropna()


def portfolio_returns(prices: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted daily returns for a static-weights portfolio.

    Args:
        prices: Wide DataFrame of close prices (columns = tickers).
        weights: Ticker -> weight (any units; will be renormalized to sum=1
            over the tickers actually present in ``prices``).

    Returns:
        Daily portfolio simple returns.
    """
    cols = [c for c in prices.columns if c in weights and weights[c] != 0]
    if not cols:
        return pd.Series(dtype=float)
    sub = prices[cols].dropna(how="all")
    rets = sub.pct_change().dropna(how="any")
    w = np.array([weights[c] for c in cols], dtype=float)
    w = w / w.sum()
    return rets.dot(w)


def cumulative_curve(returns: pd.Series, starting_value: float = 1.0) -> pd.Series:
    """Cumulative wealth index from a returns series."""
    return starting_value * (1.0 + returns).cumprod()


def compute_curves(
    portfolio_rets: pd.Series, benchmark_rets: pd.Series
) -> pd.DataFrame:
    """Two-column ``Portfolio`` / ``Benchmark`` cumulative growth chart input."""
    aligned = pd.concat(
        [portfolio_rets.rename("Portfolio"), benchmark_rets.rename("Benchmark")],
        axis=1,
    ).dropna()
    if aligned.empty:
        return aligned
    growth = (1.0 + aligned).cumprod()
    return growth


def snapshot(
    portfolio_rets: pd.Series,
    benchmark_rets: pd.Series,
    *,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> PerformanceSnapshot:
    """Compute a full PerformanceSnapshot for the given aligned return series."""
    aligned = pd.concat(
        [portfolio_rets.rename("p"), benchmark_rets.rename("b")], axis=1
    ).dropna()
    p = aligned["p"].to_numpy()
    b = aligned["b"].to_numpy()
    n = len(p)
    twr = chain_twr(p)
    bench = chain_twr(b)
    return PerformanceSnapshot(
        twr=twr,
        twr_annualized=annualize_return(twr, n, periods_per_year),
        benchmark_twr=bench,
        alpha=(1.0 + twr) / (1.0 + bench) - 1.0 if (1.0 + bench) != 0 else float("nan"),
        volatility=annualized_volatility(p, periods_per_year),
        tracking_error=tracking_error(p, b, periods_per_year),
        beta=beta(p, b),
        sharpe=sharpe_ratio(p, risk_free_rate, periods_per_year),
        sortino=sortino_ratio(p, risk_free_rate, periods_per_year),
        information_ratio=information_ratio(p, b, periods_per_year),
        max_drawdown=max_drawdown(p),
        n_observations=n,
    )


def inception_to_date(
    portfolio_rets: pd.Series, asof: datetime | None = None
) -> float:
    """Total compounded return from the first available observation to ``asof``."""
    s = portfolio_rets if asof is None else portfolio_rets.loc[:asof]  # type: ignore[misc]
    return chain_twr(s.to_list())
