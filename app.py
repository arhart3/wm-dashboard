"""WM Growth Portfolio dashboard — Streamlit entry point.

Run with:
    streamlit run app.py

The five pages live as inline functions wired into ``st.navigation``. Each
page is responsible for reading its own data; the workbook is the ground truth
and is re-read on every full page load (Streamlit's rerun model).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from wm_dashboard import institutional_style as style
from wm_dashboard.ips_check import (
    Breach,
    IpsConfig,
    check_portfolio,
    load_ips,
    only_breaches,
)
from wm_dashboard.prices import Quote, history, latest_prices, repo_snapshot_age
from wm_dashboard.reports_index import list_reports
from wm_dashboard.tracker import Portfolio, load_portfolio
from wm_dashboard.twr import compute_curves, portfolio_returns, snapshot
from wm_dashboard.whatif import Trade, simulate_trade

logging.basicConfig(level=logging.WARNING)

PROJECT_ROOT = Path(__file__).resolve().parent
PORTFOLIO_DIR = Path("/Users/aaronhart/Desktop/Claude Portfolio")
WORKBOOK = PORTFOLIO_DIR / "WM_Growth_Portfolio_Apr2026.xlsx"
IPS_DOCX = PORTFOLIO_DIR / "WM_Growth_Portfolio_IPS_v1.0.docx"
CONFIG_DIR = PROJECT_ROOT / "config"


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------


@st.cache_data(ttl=900)
def _load_portfolio() -> Portfolio:
    return load_portfolio(WORKBOOK, targets_path=CONFIG_DIR / "targets.yaml")


@st.cache_data(ttl=900)
def _load_ips() -> IpsConfig:
    return load_ips(CONFIG_DIR / "ips.yaml")


@st.cache_data(ttl=900)
def _load_benchmark() -> dict:
    with (CONFIG_DIR / "benchmarks.yaml").open() as fh:
        return yaml.safe_load(fh)["benchmark"]


@st.cache_data(ttl=900)
def _load_triggers() -> list[dict]:
    with (CONFIG_DIR / "triggers.yaml").open() as fh:
        return (yaml.safe_load(fh) or {}).get("triggers", []) or []


@st.cache_data(ttl=900)
def _load_quotes(tickers: tuple[str, ...]) -> dict[str, Quote]:
    return latest_prices(list(tickers))


@st.cache_data(ttl=3600)
def _load_history(tickers: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    return history(list(tickers), start, end)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inject_css() -> None:
    st.markdown(style.CSS, unsafe_allow_html=True)


def _format_pct(x: float | None, places: int = 2, signed: bool = False) -> str:
    if x is None or pd.isna(x):
        return "—"
    fmt = f"{{:+.{places}f}}%" if signed else f"{{:.{places}f}}%"
    return fmt.format(x)


def _format_money(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"${x:,.2f}"


def _drift_class(drift: float | None, tolerance: float) -> str:
    if drift is None:
        return ""
    return "drift-breach" if abs(drift) > tolerance else ""


def _humanize_age(delta) -> str:
    """Render a timedelta as 'just now' / '5 min ago' / '2 h ago' / '3 d ago'."""
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def _source_badge(source: str) -> str:
    """Small uppercased badge showing the upstream provider."""
    s = (source or "").lower()
    color = {
        "finnhub": "var(--ink)",
        "yfinance": "var(--subtle)",
        "stale": "var(--breach, #A23A2E)",
    }.get(s, "var(--subtle)")
    weight = "700" if s == "stale" else "500"
    return (
        f"<span style='font-size:0.62rem;font-family:Consolas,monospace;"
        f"color:{color};letter-spacing:0.06em;font-weight:{weight};"
        f"text-transform:uppercase;margin-left:4px'>{s or 'src'}</span>"
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def page_dashboard() -> None:
    portfolio = _load_portfolio()
    ips = _load_ips()
    bench_cfg = _load_benchmark()
    triggers = _load_triggers()
    tickers = tuple(p.ticker for p in portfolio.equity_positions if p.ticker not in {"CASH"})
    quotes = _load_quotes(tickers + (bench_cfg["ticker"],))

    bench_quote = quotes.get(bench_cfg["ticker"])

    snapshot_meta = repo_snapshot_age()
    if snapshot_meta is not None:
        gen, age = snapshot_meta
        prices_age_str = f"prices {_humanize_age(age)} (snapshot {gen:%Y-%m-%d %H:%M} UTC)"
    else:
        prices_age_str = "prices: live yfinance fallback (no repo snapshot)"

    st.markdown(
        f"<h1>WM Growth Portfolio</h1>"
        f"<div class='tag-sourced'>Workbook {portfolio.asof:%Y-%m-%d %H:%M} · "
        f"refreshed {datetime.now():%H:%M:%S} · {prices_age_str}</div>",
        unsafe_allow_html=True,
    )

    bench_price = f"{bench_quote.price:,.2f}" if bench_quote and bench_quote.price else "—"
    bench_chg = (
        _format_pct(bench_quote.change_pct, signed=True) if bench_quote and bench_quote.change_pct is not None else "—"
    )
    bench_prov = bench_quote.provenance if bench_quote else "STALE"

    weight_total = portfolio.total_weight_pct
    weight_footer = (
        "reconciles to 100%" if abs(weight_total - 100.0) < 0.01 else "off by " + f"{weight_total - 100.0:+.2f}%"
    )
    breach_count = sum(1 for b in check_portfolio(portfolio, ips) if b.is_breach)
    breach_footer = "all checks pass" if breach_count == 0 else "see panel below"

    kpi_html = (
        "<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0 18px 0'>"
        + "".join(
            f"<div class='card' style='margin:0;padding:10px 14px'>"
            f"<div class='tag-sourced'>{label}</div>"
            f"<div class='num' style='font-size:1.3rem;color:var(--ink)'>{value}</div>"
            f"<div class='tag-sourced'>{footer}</div>"
            f"</div>"
            for label, value, footer in [
                ("Positions", str(len(portfolio.equity_positions)), f"+ cash {portfolio.cash_pct:.1f}%"),
                ("Total weight", f"{weight_total:.2f}%", weight_footer),
                (bench_cfg["label"], bench_price, f"{bench_chg} · {bench_prov}"),
                ("IPS breaches", str(breach_count), breach_footer),
            ]
        )
        + "</div>"
    )
    st.markdown(kpi_html, unsafe_allow_html=True)

    st.markdown("### Positions")
    rows = []
    for pos in portfolio.positions:
        if pos.is_cash:
            rows.append(
                {
                    "Ticker": pos.ticker,
                    "Sector": pos.sector or "Cash",
                    "Target %": _format_pct(pos.target_weight_pct),
                    "Current %": _format_pct(pos.current_weight_pct),
                    "Drift": _format_pct(pos.drift_pct, signed=True),
                    "Last Price": "—",
                    "Day %": "—",
                    "Cost basis": "—",
                    "Unrealized $/%": "—",
                    "Provenance": "",
                }
            )
            continue
        q = quotes.get(pos.ticker)
        last_price = q.price if q else None
        day_pct = q.change_pct if q else None
        prov = q.provenance if q else "STALE"
        src = q.source if q else "stale"
        unreal = "—"
        if pos.cost_basis and last_price:
            pl_pct = (last_price / pos.cost_basis - 1.0) * 100.0
            unreal = f"{_format_money(last_price - pos.cost_basis)} / {_format_pct(pl_pct, signed=True)}"
        rows.append(
            {
                "Ticker": pos.ticker,
                "Sector": (pos.sector or "").split("–")[0].strip(),
                "Target %": _format_pct(pos.target_weight_pct),
                "Current %": _format_pct(pos.current_weight_pct),
                "Drift": _format_pct(pos.drift_pct, signed=True),
                "Last Price": _format_money(last_price),
                "Day %": _format_pct(day_pct, signed=True),
                "Cost basis": _format_money(pos.cost_basis),
                "Unrealized $/%": unreal,
                "Provenance": prov,
                "Source": src,
            }
        )

    # Hand-rolled HTML table so we can color drift cells and tag provenance.
    table_rows = []
    for r in rows:
        cls = ""
        try:
            d_val = float(r["Drift"].replace("%", "").replace("+", ""))
            if abs(d_val) > ips.rebalance_tolerance_pct:
                cls = "drift-breach"
        except (ValueError, AttributeError):
            pass
        src_html = _source_badge(r.get("Source", "")) if r.get("Source") else ""
        table_rows.append(
            f"<tr class='{cls}'>"
            f"<td>{r['Ticker']}</td>"
            f"<td>{r['Sector']}</td>"
            f"<td class='num'>{r['Target %']}</td>"
            f"<td class='num'>{r['Current %']}</td>"
            f"<td class='num drift'>{r['Drift']}</td>"
            f"<td class='num'>{r['Last Price']}{src_html}</td>"
            f"<td class='num'>{r['Day %']}</td>"
            f"<td class='num'>{r['Cost basis']}</td>"
            f"<td class='num'>{r['Unrealized $/%']}</td>"
            f"</tr>"
        )
    st.markdown(
        "<table>"
        "<thead><tr>"
        "<th>Ticker</th><th>Sector</th><th>Target %</th><th>Current %</th>"
        "<th>Drift</th><th>Last Price</th><th>Day %</th>"
        "<th>Cost basis</th><th>Unrealized $/%</th>"
        "</tr></thead><tbody>"
        + "".join(table_rows)
        + "</tbody></table>",
        unsafe_allow_html=True,
    )

    left, right = st.columns([3, 2])
    with left:
        st.markdown("### IPS Status")
        breaches = check_portfolio(portfolio, ips)
        _render_ips_panel(breaches)
    with right:
        st.markdown("### Active Triggers")
        if not triggers:
            st.markdown("_No active triggers in `config/triggers.yaml`._")
        from datetime import date as _date
        today = _date.today()
        for t in triggers:
            review_by_raw = t.get("review_by")
            stale = False
            if isinstance(review_by_raw, _date):
                stale = review_by_raw < today
                review_by = review_by_raw.isoformat()
            elif isinstance(review_by_raw, str):
                try:
                    stale = _date.fromisoformat(review_by_raw) < today
                except ValueError:
                    pass
                review_by = review_by_raw
            else:
                review_by = "—"
            stale_tag = " <span class='tag-stale'>past due</span>" if stale else ""
            card_cls = "card card-breach" if stale else "card card-review"
            st.markdown(
                f"<div class='{card_cls}'>"
                f"<strong>{t.get('ticker', '?')}</strong> "
                f"<span class='tag-sourced'>conf {t.get('confidence', '—')} · "
                f"review by {review_by}</span>{stale_tag}<br>"
                f"<span class='subtle' style='font-size:0.86rem'>{t.get('description', '')}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )


_AGG_PREFIXES = ("Sector cap", "Cash band", "Tracking", "Beta", "Max drawdown", "Annualized", "Leverage")


def _is_aggregate(b: Breach) -> bool:
    return b.field.startswith(_AGG_PREFIXES)


def _fmt(v: float | str, places: int = 2) -> str:
    return v if isinstance(v, str) else f"{v:.{places}f}"


def _render_ips_panel(breaches: list[Breach]) -> None:
    aggregate = [b for b in breaches if _is_aggregate(b)]
    position = [b for b in breaches if not _is_aggregate(b) and b.severity != "OK"]

    rows = "".join(
        f"<tr>"
        f"<td>{style.status_pill(b.severity)}</td>"
        f"<td>{b.field}</td>"
        f"<td class='num'>{_fmt(b.actual)}</td>"
        f"<td class='num'>{_fmt(b.limit)}</td>"
        f"</tr>"
        for b in aggregate
    )
    st.markdown(
        "<table>"
        "<thead><tr><th></th><th>Constraint</th><th>Actual</th><th>Limit</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>",
        unsafe_allow_html=True,
    )

    if position:
        st.markdown(f"<div class='tag-sourced' style='margin-top:14px'>Position-level exceptions ({len(position)})</div>", unsafe_allow_html=True)
        rows = "".join(
            f"<tr>"
            f"<td>{style.status_pill(b.severity)}</td>"
            f"<td>{b.field}</td>"
            f"<td class='num'>{_fmt(b.actual)}</td>"
            f"<td class='num'>{_fmt(b.limit)}</td>"
            f"</tr>"
            for b in position[:20]
        )
        st.markdown(
            "<table>"
            "<thead><tr><th></th><th>Position check</th><th>Actual</th><th>Limit</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='tag-sourced' style='margin-top:14px'>No position-level exceptions.</div>",
            unsafe_allow_html=True,
        )


def page_performance() -> None:
    portfolio = _load_portfolio()
    ips = _load_ips()
    bench_cfg = _load_benchmark()

    st.markdown("## Performance")
    inception = datetime(2026, 4, 9)
    today = datetime.now()
    c1, c2 = st.columns(2)
    with c1:
        start = st.date_input("Start", value=inception.date(), max_value=today.date())
    with c2:
        end = st.date_input("End", value=today.date(), max_value=today.date())
    if end <= start:
        st.warning("End date must be after start date.")
        return

    weights = {
        p.ticker: p.current_weight_pct
        for p in portfolio.positions
        if not p.is_cash and p.current_weight_pct > 0
    }
    tickers = tuple(weights.keys())
    bench_ticker = bench_cfg["ticker"]

    with st.spinner("Loading prices..."):
        prices = _load_history(tickers + (bench_ticker,), str(start), str(end))

    if prices.empty:
        st.error("No price data returned. Check yfinance connectivity.")
        return

    bench_rets = prices[bench_ticker].pct_change().dropna() if bench_ticker in prices.columns else pd.Series(dtype=float)
    port_rets = portfolio_returns(prices.drop(columns=[bench_ticker], errors="ignore"), weights)

    if port_rets.empty or bench_rets.empty:
        st.error("Insufficient overlapping data between portfolio and benchmark.")
        return

    snap = snapshot(port_rets, bench_rets)
    curves = compute_curves(port_rets, bench_rets)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=curves.index, y=curves["Portfolio"], name="Portfolio", mode="lines"))
    fig.add_trace(go.Scatter(x=curves.index, y=curves["Benchmark"], name=bench_cfg["label"], mode="lines"))
    fig.update_layout(**style.grayscale_layout(title=f"Cumulative growth · {snap.n_observations} obs"))
    style.style_lines(fig)
    st.plotly_chart(fig, width="stretch")

    # Each metric: (label, formatted value, footer, severity).
    # Severity drives the colored left-border on the card. None = no badge.
    def _ceiling_sev(actual: float, limit: float, buf: float = 0.25) -> str:
        if actual > limit:
            return "BREACH"
        if actual >= limit - buf:
            return "REVIEW"
        return "OK"

    def _floor_sev(actual: float, limit: float, buf: float = 0.25) -> str:
        if actual < limit:
            return "BREACH"
        if actual <= limit + buf:
            return "REVIEW"
        return "OK"

    def _band_sev(actual: float, lo: float, hi: float, buf: float = 0.02) -> str:
        if actual < lo or actual > hi:
            return "BREACH"
        if actual <= lo + buf or actual >= hi - buf:
            return "REVIEW"
        return "OK"

    vol_pct = snap.volatility * 100
    te_pct = snap.tracking_error * 100
    mdd_pct = snap.max_drawdown * 100
    metrics = [
        ("TWR (period)", _format_pct(snap.twr * 100, signed=True), None, None),
        ("TWR annualized", _format_pct(snap.twr_annualized * 100, signed=True), None, None),
        ("Benchmark TWR", _format_pct(snap.benchmark_twr * 100, signed=True), None, None),
        ("Geometric alpha", _format_pct(snap.alpha * 100, signed=True), None, None),
        ("Volatility", _format_pct(vol_pct), f"limit {ips.max_volatility_pct:.1f}%", _ceiling_sev(vol_pct, ips.max_volatility_pct)),
        ("Tracking error", _format_pct(te_pct), f"limit {ips.tracking_error_ceiling_pct:.1f}%", _ceiling_sev(te_pct, ips.tracking_error_ceiling_pct)),
        ("Beta", f"{snap.beta:.2f}", f"band {ips.beta_min:.2f}-{ips.beta_max:.2f}", _band_sev(snap.beta, ips.beta_min, ips.beta_max)),
        ("Sharpe", f"{snap.sharpe:.2f}", None, None),
        ("Sortino", f"{snap.sortino:.2f}", None, None),
        ("Information ratio", f"{snap.information_ratio:.2f}", None, None),
        ("Max drawdown", _format_pct(mdd_pct), f"limit {ips.max_drawdown_pct:.1f}%", _floor_sev(mdd_pct, ips.max_drawdown_pct)),
    ]

    st.markdown("### Risk metrics")
    per_row = 4
    for chunk_start in range(0, len(metrics), per_row):
        chunk = metrics[chunk_start : chunk_start + per_row]
        cols = st.columns(per_row)
        for col, (label, value, footnote, sev) in zip(cols, chunk, strict=False):
            with col:
                foot = f"<div class='tag-sourced'>{footnote}</div>" if footnote else "<div class='tag-sourced'>&nbsp;</div>"
                pill = f"&nbsp;{style.status_pill(sev)}" if sev and sev != "OK" else ""
                card_cls = f"card card-{sev.lower()}" if sev else "card"
                st.markdown(
                    f"<div class='{card_cls}'>"
                    f"<div class='tag-sourced'>{label}{pill}</div>"
                    f"<div class='num' style='font-size:1.35rem;color:var(--ink)'>{value}</div>"
                    f"{foot}</div>",
                    unsafe_allow_html=True,
                )

    low_n_warning = (
        " <strong>Low-n caveat:</strong> with fewer than 30 observations the "
        "risk metrics (vol, TE, beta, Sharpe, Sortino, IR) carry low statistical "
        "confidence and should be read as directional only."
        if snap.n_observations < 30
        else ""
    )
    st.markdown(
        f"<div class='tag-sourced' style='margin-top:14px'>n = {snap.n_observations} "
        f"daily observations.{low_n_warning}</div>",
        unsafe_allow_html=True,
    )


def page_whatif() -> None:
    portfolio = _load_portfolio()
    ips = _load_ips()
    st.markdown("## What-If Trade")
    st.markdown(
        "<div class='tag-sourced'>Stages a trade in session memory only. "
        "Does not write to the workbook.</div>",
        unsafe_allow_html=True,
    )

    tickers = sorted({p.ticker for p in portfolio.positions if not p.is_cash})
    options = tickers + ["(new ticker)"]

    with st.form("whatif_form"):
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            choice = st.selectbox("Ticker", options=options, index=0)
        with c2:
            action = st.selectbox("Action", options=["BUY", "SELL"])
        with c3:
            size = st.number_input("Size %", min_value=0.0, max_value=20.0, value=0.5, step=0.1)

        new_ticker = ""
        new_sector = ""
        is_etf = False
        if choice == "(new ticker)":
            n1, n2, n3 = st.columns([1, 2, 1])
            with n1:
                new_ticker = st.text_input("New ticker").upper().strip()
            with n2:
                new_sector = st.text_input("Sector (required for new tickers)")
            with n3:
                is_etf = st.checkbox("ETF (5% cap)")

        pre_mortem = st.text_area(
            "Pre-mortem (required, min 20 chars) — 'wrong if ___'",
            placeholder="Wrong if Q1 misses MA guidance and forces a re-rate within 2 sessions.",
            height=80,
        )
        confirm = st.form_submit_button("Confirm (stage trade)")

    if not confirm:
        st.info("Fill the form and click Confirm to stage a trade.")
        return

    ticker = new_ticker if choice == "(new ticker)" else choice
    if not ticker:
        st.error("Ticker is required.")
        return
    if size <= 0:
        st.error("Size must be > 0.")
        return

    trade = Trade(
        ticker=ticker,
        action=action,
        size_pct=float(size),
        sector=new_sector or None,
        is_etf=is_etf,
        pre_mortem=pre_mortem,
    )
    try:
        result = simulate_trade(portfolio, trade, ips)
    except ValueError as exc:
        st.error(f"Trade rejected: {exc}")
        return

    st.session_state["last_whatif"] = result

    if result.pre_mortem_warning:
        st.markdown(
            f"<div class='card card-review'>{style.status_pill('REVIEW')} "
            f"{result.pre_mortem_warning}</div>",
            unsafe_allow_html=True,
        )

    breaches = only_breaches(result.breaches)
    if not breaches:
        st.markdown(
            f"<div class='card card-ok'>{style.status_pill('OK')} <strong>All IPS checks pass.</strong></div>",
            unsafe_allow_html=True,
        )
    else:
        for b in breaches:
            st.markdown(
                f"<div class='card card-breach'>{style.status_pill('BREACH')} "
                f"<strong>{b.field}</strong>"
                f"<div class='num'>actual {b.actual} · limit {b.limit}</div></div>",
                unsafe_allow_html=True,
            )

    st.markdown("### Post-trade weights")
    pre = {p.ticker: p.current_weight_pct for p in portfolio.positions}
    rows_html = ""
    for p in result.post_portfolio.positions:
        before = pre.get(p.ticker, 0.0)
        delta = p.current_weight_pct - before
        delta_str = "" if abs(delta) < 1e-9 else f" <span class='num'>({delta:+.2f}%)</span>"
        rows_html += (
            f"<tr><td>{p.ticker}</td><td>{(p.sector or '').split('–')[0].strip()}</td>"
            f"<td class='num'>{before:.2f}%</td>"
            f"<td class='num'>{p.current_weight_pct:.2f}%{delta_str}</td></tr>"
        )
    st.markdown(
        "<table><thead><tr><th>Ticker</th><th>Sector</th><th>Pre %</th><th>Post %</th></tr></thead><tbody>"
        + rows_html
        + "</tbody></table>",
        unsafe_allow_html=True,
    )


def page_reports() -> None:
    st.markdown("## Reports")
    reports = list_reports(PORTFOLIO_DIR)
    if not reports:
        st.markdown(
            "<div class='card'>"
            "No reports found. On the cloud deployment, reports live in "
            "<code>data/reports/</code> in the repo — sync new files with "
            "<code>python scripts/sync_reports.py</code> and push."
            "</div>",
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        "<div class='tag-sourced'>"
        "Cloud reports are downloaded from GitHub on click; local reports open in Word."
        "</div>",
        unsafe_allow_html=True,
    )
    for kind in ("Daily", "Weekly", "Quarterly", "IPS"):
        items = [r for r in reports if r.kind == kind]
        if not items:
            continue
        st.markdown(f"### {kind}")
        rows_html = "".join(
            f"<tr>"
            f"<td><strong>{r.label}</strong></td>"
            f"<td class='tag-sourced'>modified {r.modified:%Y-%m-%d %H:%M} · "
            f"{'repo' if r.in_repo else 'local'}</td>"
            f"<td class='num'>"
            f"<a href='{r.file_url}' target='_blank' rel='noopener' "
            f"style='display:inline-block;padding:2px 12px;background:var(--ink);"
            f"color:var(--paper) !important;text-decoration:none;font-size:0.72rem;"
            f"font-weight:600;letter-spacing:0.06em;text-transform:uppercase;border-radius:2px'>"
            f"Open</a></td>"
            f"</tr>"
            for r in items
        )
        st.markdown(
            "<table><thead><tr>"
            "<th>Report</th><th>Source &amp; modified</th><th>Link</th>"
            "</tr></thead>"
            f"<tbody>{rows_html}</tbody></table>",
            unsafe_allow_html=True,
        )


def page_ips() -> None:
    st.markdown("## IPS")
    ips = _load_ips()
    st.markdown(
        f"<div class='tag-sourced'>Source of truth: "
        f"<code>{IPS_DOCX.name}</code> · operational copy in "
        f"<code>config/ips.yaml</code></div>",
        unsafe_allow_html=True,
    )
    if IPS_DOCX.exists():
        st.link_button("Open IPS .docx", url=IPS_DOCX.as_uri())

    rows = [
        ("Max single equity position", f"{ips.max_position_equity_pct:.1f}%"),
        ("Max single ETF position", f"{ips.max_position_etf_pct:.1f}%"),
        ("Max sector weight", f"{ips.max_sector_pct:.1f}%"),
        ("Cash band", f"{ips.cash_band_min_pct:.1f}-{ips.cash_band_max_pct:.1f}%"),
        ("Rebalance tolerance", f"±{ips.rebalance_tolerance_pct:.1f}%"),
        ("Tracking error ceiling (60d)", f"{ips.tracking_error_ceiling_pct:.1f}%"),
        ("Beta band (60d OLS)", f"{ips.beta_min:.2f}-{ips.beta_max:.2f}"),
        ("Max drawdown", f"{ips.max_drawdown_pct:.1f}%"),
        ("Max annualized volatility", f"{ips.max_volatility_pct:.1f}%"),
        ("Leverage", "Disallowed" if ips.no_leverage else "Permitted"),
        ("Review buffer (severity)", f"{ips.review_buffer_pct:.2f}%"),
    ]
    body = "".join(
        f"<tr><td>{label}</td><td class='num'>{value}</td></tr>" for label, value in rows
    )
    st.markdown(
        "<table><thead><tr><th>Constraint</th><th>Limit</th></tr></thead><tbody>"
        + body
        + "</tbody></table>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="WM Growth Portfolio",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_css()

    pages = [
        st.Page(page_dashboard, title="Dashboard", default=True),
        st.Page(page_performance, title="Performance"),
        st.Page(page_whatif, title="What-If Trade"),
        st.Page(page_reports, title="Reports"),
        st.Page(page_ips, title="IPS"),
    ]
    nav = st.navigation(pages, position="sidebar")
    nav.run()


main()
