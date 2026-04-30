"""IPS constraint checks.

Constraints come from ``config/ips.yaml``. The file is the operational copy of
the limits encoded in ``WM_Growth_Portfolio_IPS_v1.0.docx``.

Severity model:
- ``OK``     — within the limit by more than ``review_buffer_pct``.
- ``REVIEW`` — within ``review_buffer_pct`` of the limit but not yet over.
- ``BREACH`` — over the hard limit.

Each check returns zero-or-more ``Breach`` records; an empty list means clean.
The dashboard groups breaches by ``field`` to render the IPS panel.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from .tracker import Portfolio, Position

Severity = Literal["OK", "REVIEW", "BREACH"]


@dataclass(frozen=True)
class Breach:
    """One constraint result. ``OK`` records are also returned so the UI can
    render the full panel; the dashboard filters by severity as needed."""

    field: str
    limit: float | str
    actual: float | str
    severity: Severity
    detail: str = ""

    @property
    def is_breach(self) -> bool:
        return self.severity == "BREACH"


@dataclass(frozen=True)
class IpsConfig:
    """Strongly-typed view of ``config/ips.yaml``."""

    max_position_equity_pct: float
    max_position_etf_pct: float
    max_sector_pct: float
    cash_band_min_pct: float
    cash_band_max_pct: float
    rebalance_tolerance_pct: float
    tracking_error_ceiling_pct: float
    beta_min: float
    beta_max: float
    max_drawdown_pct: float
    max_volatility_pct: float
    no_leverage: bool
    review_buffer_pct: float = 0.25


def load_ips(path: Path) -> IpsConfig:
    """Load and validate the IPS yaml file."""
    with path.open() as fh:
        raw = yaml.safe_load(fh) or {}
    return IpsConfig(
        max_position_equity_pct=float(raw["max_position_equity_pct"]),
        max_position_etf_pct=float(raw["max_position_etf_pct"]),
        max_sector_pct=float(raw["max_sector_pct"]),
        cash_band_min_pct=float(raw["cash_band_min_pct"]),
        cash_band_max_pct=float(raw["cash_band_max_pct"]),
        rebalance_tolerance_pct=float(raw["rebalance_tolerance_pct"]),
        tracking_error_ceiling_pct=float(raw["tracking_error_ceiling_pct"]),
        beta_min=float(raw["beta_min"]),
        beta_max=float(raw["beta_max"]),
        max_drawdown_pct=float(raw["max_drawdown_pct"]),
        max_volatility_pct=float(raw["max_volatility_pct"]),
        no_leverage=bool(raw["no_leverage"]),
        review_buffer_pct=float(raw.get("review_buffer_pct", 0.25)),
    )


def _ceiling_severity(actual: float, limit: float, buffer: float) -> Severity:
    """Severity for an upper-bound check (actual must stay <= limit)."""
    if actual > limit:
        return "BREACH"
    if actual >= limit - buffer:
        return "REVIEW"
    return "OK"


def _floor_severity(actual: float, limit: float, buffer: float) -> Severity:
    """Severity for a lower-bound check (actual must stay >= limit)."""
    if actual < limit:
        return "BREACH"
    if actual <= limit + buffer:
        return "REVIEW"
    return "OK"


def check_position(position: Position, ips: IpsConfig) -> list[Breach]:
    """Run per-position checks (size cap + drift)."""
    out: list[Breach] = []
    if position.is_cash:
        return out
    cap = ips.max_position_etf_pct if position.is_etf else ips.max_position_equity_pct
    field = f"{position.ticker} size ({'ETF' if position.is_etf else 'equity'} cap)"
    out.append(
        Breach(
            field=field,
            limit=cap,
            actual=position.current_weight_pct,
            severity=_ceiling_severity(position.current_weight_pct, cap, ips.review_buffer_pct),
        )
    )
    drift = position.drift_pct
    if drift is not None:
        actual_abs = abs(drift)
        out.append(
            Breach(
                field=f"{position.ticker} drift",
                limit=ips.rebalance_tolerance_pct,
                actual=actual_abs,
                severity=_ceiling_severity(actual_abs, ips.rebalance_tolerance_pct, ips.review_buffer_pct),
                detail=f"target={position.target_weight_pct:.2f}%, current={position.current_weight_pct:.2f}%",
            )
        )
    return out


def check_sectors(portfolio: Portfolio, ips: IpsConfig) -> list[Breach]:
    out: list[Breach] = []
    for sector, weight in portfolio.sector_totals.items():
        out.append(
            Breach(
                field=f"Sector cap — {sector}",
                limit=ips.max_sector_pct,
                actual=weight,
                severity=_ceiling_severity(weight, ips.max_sector_pct, ips.review_buffer_pct),
            )
        )
    return out


def check_cash_band(portfolio: Portfolio, ips: IpsConfig) -> Breach:
    cash = portfolio.cash_pct
    if cash < ips.cash_band_min_pct:
        sev: Severity = "BREACH"
    elif cash > ips.cash_band_max_pct:
        sev = "BREACH"
    elif cash <= ips.cash_band_min_pct + ips.review_buffer_pct:
        sev = "REVIEW"
    elif cash >= ips.cash_band_max_pct - ips.review_buffer_pct:
        sev = "REVIEW"
    else:
        sev = "OK"
    return Breach(
        field="Cash band",
        limit=f"{ips.cash_band_min_pct:.1f}-{ips.cash_band_max_pct:.1f}%",
        actual=cash,
        severity=sev,
    )


def check_risk_metrics(
    portfolio: Portfolio,
    ips: IpsConfig,
    *,
    tracking_error_pct: float | None = None,
    beta_value: float | None = None,
    max_drawdown_pct: float | None = None,
    volatility_pct: float | None = None,
) -> list[Breach]:
    """Run the four IPS risk checks. Pass ``None`` to skip a metric."""
    out: list[Breach] = []
    if tracking_error_pct is not None:
        out.append(
            Breach(
                field="Tracking error (annualized)",
                limit=ips.tracking_error_ceiling_pct,
                actual=tracking_error_pct,
                severity=_ceiling_severity(
                    tracking_error_pct, ips.tracking_error_ceiling_pct, ips.review_buffer_pct
                ),
            )
        )
    if beta_value is not None:
        if beta_value < ips.beta_min or beta_value > ips.beta_max:
            sev: Severity = "BREACH"
        elif beta_value <= ips.beta_min + 0.02 or beta_value >= ips.beta_max - 0.02:
            sev = "REVIEW"
        else:
            sev = "OK"
        out.append(
            Breach(
                field="Beta band (60d OLS)",
                limit=f"{ips.beta_min:.2f}-{ips.beta_max:.2f}",
                actual=round(beta_value, 3),
                severity=sev,
            )
        )
    if max_drawdown_pct is not None:
        # Limit is e.g. -15.0; we breach if drawdown is MORE negative.
        sev = "OK"
        if max_drawdown_pct < ips.max_drawdown_pct:
            sev = "BREACH"
        elif max_drawdown_pct <= ips.max_drawdown_pct + ips.review_buffer_pct:
            sev = "REVIEW"
        out.append(
            Breach(
                field="Max drawdown",
                limit=ips.max_drawdown_pct,
                actual=max_drawdown_pct,
                severity=sev,
            )
        )
    if volatility_pct is not None:
        out.append(
            Breach(
                field="Annualized volatility",
                limit=ips.max_volatility_pct,
                actual=volatility_pct,
                severity=_ceiling_severity(
                    volatility_pct, ips.max_volatility_pct, ips.review_buffer_pct
                ),
            )
        )
    return out


def check_portfolio(
    portfolio: Portfolio,
    ips: IpsConfig,
    *,
    tracking_error_pct: float | None = None,
    beta_value: float | None = None,
    max_drawdown_pct: float | None = None,
    volatility_pct: float | None = None,
) -> list[Breach]:
    """Run all checks on a portfolio. Returns OK + REVIEW + BREACH records."""
    breaches: list[Breach] = []
    for pos in portfolio.positions:
        breaches.extend(check_position(pos, ips))
    breaches.extend(check_sectors(portfolio, ips))
    breaches.append(check_cash_band(portfolio, ips))
    breaches.extend(
        check_risk_metrics(
            portfolio,
            ips,
            tracking_error_pct=tracking_error_pct,
            beta_value=beta_value,
            max_drawdown_pct=max_drawdown_pct,
            volatility_pct=volatility_pct,
        )
    )
    if ips.no_leverage:
        breaches.append(
            Breach(
                field="Leverage (sum of weights)",
                limit=100.0,
                actual=round(portfolio.total_weight_pct, 2),
                severity=_ceiling_severity(
                    portfolio.total_weight_pct, 100.0 + 0.5, ips.review_buffer_pct
                ),
            )
        )
    return breaches


def only_breaches(breaches: list[Breach]) -> list[Breach]:
    """Filter to BREACH-severity records (useful for the what-if page)."""
    return [b for b in breaches if b.is_breach]
