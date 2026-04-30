"""TWR + risk-attribution tests. Reproduces the confirmed inception number."""

from __future__ import annotations

import numpy as np
import pandas as pd

from wm_dashboard.risk_attribution import (
    annualized_volatility,
    beta,
    chain_twr,
    information_ratio,
    max_drawdown,
    tracking_error,
)
from wm_dashboard.twr import compute_curves, daily_returns, snapshot


def test_chain_twr_inception_matches_confirmed_4dp():
    # SP1 (Apr 9-10 pre-rebalance) and SP2 (Apr 13-16 post-rebalance) from
    # .memory/wm_portfolio.md. Confirmed aggregate is +4.867%.
    twr = chain_twr([0.01793, 0.03020])
    assert round(twr, 5) == 0.04867
    assert round(twr, 4) == 0.0487


def test_chain_twr_empty_is_zero():
    assert chain_twr([]) == 0.0


def test_chain_twr_single_period_is_identity():
    assert round(chain_twr([0.025]), 10) == 0.025


def test_chain_twr_three_periods_compounds():
    # 1.01 * 1.02 * 1.03 - 1
    expected = 1.01 * 1.02 * 1.03 - 1.0
    assert round(chain_twr([0.01, 0.02, 0.03]), 10) == round(expected, 10)


def test_max_drawdown_is_negative():
    rets = [0.05, 0.02, -0.10, -0.05, 0.03]
    dd = max_drawdown(rets)
    assert dd < 0


def test_annualized_vol_matches_manual_calc():
    rets = [0.01, -0.005, 0.012, -0.008, 0.004]
    expected = float(np.std(rets, ddof=1) * np.sqrt(252))
    assert annualized_volatility(rets) == expected


def test_beta_against_self_is_one():
    rets = [0.01, 0.02, -0.01, 0.005, 0.0, 0.012]
    assert round(beta(rets, rets), 6) == 1.0


def test_tracking_error_against_self_is_zero():
    rets = [0.01, 0.02, -0.01, 0.005, 0.0, 0.012]
    assert tracking_error(rets, rets) == 0.0


def test_information_ratio_against_self_is_nan():
    rets = [0.01, 0.02, -0.01, 0.005, 0.0, 0.012]
    ir = information_ratio(rets, rets)
    assert np.isnan(ir)


def test_daily_returns_drops_first_observation():
    s = pd.Series([100, 101, 102, 103])
    r = daily_returns(s)
    assert len(r) == 3


def test_compute_curves_aligns_and_grows():
    idx = pd.date_range("2026-04-09", periods=4, freq="B")
    p = pd.Series([0.01, 0.005, -0.002, 0.012], index=idx)
    b = pd.Series([0.008, 0.003, 0.0, 0.011], index=idx)
    curves = compute_curves(p, b)
    assert list(curves.columns) == ["Portfolio", "Benchmark"]
    assert curves.iloc[0]["Portfolio"] == 1.01
    assert curves.iloc[-1]["Portfolio"] > 1.0


def test_snapshot_produces_full_metrics_block():
    idx = pd.date_range("2026-04-09", periods=10, freq="B")
    p = pd.Series([0.01, 0.005, -0.002, 0.012, -0.001, 0.003, 0.007, -0.004, 0.002, 0.006], index=idx)
    b = pd.Series([0.008, 0.003, 0.0, 0.011, -0.002, 0.002, 0.006, -0.003, 0.001, 0.005], index=idx)
    snap = snapshot(p, b, risk_free_rate=0.04)
    assert snap.n_observations == 10
    assert snap.twr > 0
    assert snap.benchmark_twr > 0
