"""Stage a hypothetical trade and re-run the IPS check.

The whatif module never mutates the workbook. It produces a *new* Portfolio
object reflecting the post-trade weights, then runs ``ips_check.check_portfolio``
against it. The dashboard's What-If page renders the result as a card panel.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Literal

from .ips_check import Breach, IpsConfig, check_portfolio
from .tracker import Portfolio, Position

Action = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Trade:
    """A staged (un-executed) trade. ``size_pct`` is in percentage points."""

    ticker: str
    action: Action
    size_pct: float
    sector: str | None = None  # required when introducing a new ticker
    is_etf: bool = False
    pre_mortem: str = ""

    @property
    def signed_delta(self) -> float:
        """Signed change in target weight (positive for BUY, negative for SELL)."""
        return self.size_pct if self.action == "BUY" else -self.size_pct


@dataclass(frozen=True)
class WhatIfResult:
    """Output of ``simulate_trade``. ``post_portfolio`` is a deep copy."""

    trade: Trade
    pre_portfolio: Portfolio
    post_portfolio: Portfolio
    breaches: list[Breach]
    pre_mortem_warning: str | None


def _find_or_make_position(
    portfolio: Portfolio, ticker: str, sector: str | None, is_etf: bool
) -> Position:
    existing = portfolio.by_ticker(ticker)
    if existing is not None:
        return existing
    return Position(
        ticker=ticker,
        company=None,
        sector=sector,
        current_weight_pct=0.0,
        target_weight_pct=None,
        price=None,
        cost_basis=None,
        notes=None,
        is_cash=False,
        is_etf=is_etf,
    )


def simulate_trade(
    portfolio: Portfolio,
    trade: Trade,
    ips: IpsConfig,
    *,
    cash_ticker: str = "CASH",
    min_pre_mortem_chars: int = 20,
) -> WhatIfResult:
    """Stage ``trade`` against ``portfolio`` and re-run IPS checks.

    The trade is funded from (or settled into) the cash position so the total
    weight stays at 100%. The original Portfolio is not mutated.

    Raises:
        ValueError: if a new ticker is staged without a sector, if the trade
            would push cash negative, or if a SELL exceeds the existing weight.
    """
    if trade.size_pct <= 0:
        raise ValueError("Trade size must be positive (use action=SELL to reduce).")

    pre_post = copy.deepcopy(portfolio)
    cash = pre_post.by_ticker(cash_ticker)
    if cash is None:
        raise ValueError(f"Portfolio has no {cash_ticker!r} position; cannot fund trade.")

    target = pre_post.by_ticker(trade.ticker)
    if target is None:
        if not trade.sector:
            raise ValueError(
                f"Ticker {trade.ticker} not in portfolio; supply a sector to add it."
            )
        target = _find_or_make_position(pre_post, trade.ticker, trade.sector, trade.is_etf)
        pre_post.positions.insert(-1, target)  # keep CASH at end if convention

    delta = trade.signed_delta
    new_weight = round(target.current_weight_pct + delta, 6)
    if new_weight < -1e-9:
        raise ValueError(
            f"SELL of {trade.size_pct:.2f}% exceeds {target.ticker} current "
            f"weight {target.current_weight_pct:.2f}%."
        )
    new_cash = round(cash.current_weight_pct - delta, 6)
    if new_cash < -1e-9:
        raise ValueError(
            f"BUY of {trade.size_pct:.2f}% exceeds available cash "
            f"{cash.current_weight_pct:.2f}%."
        )

    # Mutate the deep-copied positions in place.
    target.current_weight_pct = new_weight
    cash.current_weight_pct = new_cash
    pre_post.cash_pct = new_cash

    # Recompute sector totals for the post portfolio.
    sector_totals: dict[str, float] = {}
    for p in pre_post.positions:
        if p.is_cash or not p.sector:
            continue
        head = p.sector.split("–")[0].split("-")[0].strip()
        sector_totals[head] = sector_totals.get(head, 0.0) + p.current_weight_pct
    pre_post.sector_totals = sector_totals

    breaches = check_portfolio(pre_post, ips)

    warning: str | None = None
    if len(trade.pre_mortem.strip()) < min_pre_mortem_chars:
        warning = (
            f"Pre-mortem too short ({len(trade.pre_mortem.strip())} chars). "
            f"IPS §trade discipline requires a 'wrong if ___' rationale of at "
            f"least {min_pre_mortem_chars} characters."
        )

    return WhatIfResult(
        trade=trade,
        pre_portfolio=portfolio,
        post_portfolio=pre_post,
        breaches=breaches,
        pre_mortem_warning=warning,
    )
