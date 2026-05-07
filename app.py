"""WM Growth Portfolio dashboard — Streamlit entry point.

Run with:
    streamlit run app.py

The five pages live as inline functions wired into ``st.navigation``. Each
page is responsible for reading its own data; the workbook is the ground truth
and is re-read on every full page load (Streamlit's rerun model).
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta
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
#
# Every cache key includes the underlying file's mtime so a fresh write to
# prices.json / positions.yaml / triggers.yaml busts the cache immediately.
# Without this, a Streamlit Cloud container that reads a file once would
# keep serving that stale read for the full TTL (15 min), which matters
# because the cron rewrites prices.json every 30 min.


def _mtime(path: Path) -> float:
    """File mtime or 0 if absent. Used as a cache-invalidation key."""
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


@st.cache_data(ttl=900)
def _load_portfolio_cached(workbook_mtime: float, positions_mtime: float) -> Portfolio:
    return load_portfolio(WORKBOOK, targets_path=CONFIG_DIR / "targets.yaml")


def _load_portfolio() -> Portfolio:
    return _load_portfolio_cached(_mtime(WORKBOOK), _mtime(CONFIG_DIR / "positions.yaml"))


@st.cache_data(ttl=900)
def _load_ips_cached(ips_mtime: float) -> IpsConfig:
    return load_ips(CONFIG_DIR / "ips.yaml")


def _load_ips() -> IpsConfig:
    return _load_ips_cached(_mtime(CONFIG_DIR / "ips.yaml"))


@st.cache_data(ttl=900)
def _load_benchmark_cached(mtime: float) -> dict:
    with (CONFIG_DIR / "benchmarks.yaml").open() as fh:
        return yaml.safe_load(fh)["benchmark"]


def _load_benchmark() -> dict:
    return _load_benchmark_cached(_mtime(CONFIG_DIR / "benchmarks.yaml"))


@st.cache_data(ttl=900)
def _load_benchmarks_all_cached(mtime: float) -> dict:
    """Load multi-benchmark config: primary + reference list."""
    with (CONFIG_DIR / "benchmarks.yaml").open() as fh:
        raw = yaml.safe_load(fh) or {}
    return {
        "primary": raw.get("primary") or raw.get("benchmark") or {"ticker": "^GSPC", "label": "S&P 500 TR"},
        "reference": raw.get("reference") or [],
    }


def _load_benchmarks_all() -> dict:
    return _load_benchmarks_all_cached(_mtime(CONFIG_DIR / "benchmarks.yaml"))


@st.cache_data(ttl=900)
def _load_triggers_cached(mtime: float) -> list[dict]:
    with (CONFIG_DIR / "triggers.yaml").open() as fh:
        return (yaml.safe_load(fh) or {}).get("triggers", []) or []


def _load_triggers() -> list[dict]:
    return _load_triggers_cached(_mtime(CONFIG_DIR / "triggers.yaml"))


@st.cache_data(ttl=900)
def _load_quotes_cached(tickers: tuple[str, ...], prices_mtime: float) -> dict[str, Quote]:
    return latest_prices(list(tickers))


def _load_quotes(tickers: tuple[str, ...]) -> dict[str, Quote]:
    prices_path = PROJECT_ROOT / "data" / "prices.json"
    return _load_quotes_cached(tickers, _mtime(prices_path))


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


def _ticker_links_html(ticker: str) -> str:
    """Inline external-research links rendered under each ticker symbol.

    Skipped for non-tradable rows (CASH, combined ``XOM/EOG``-style triggers,
    portfolio-level pseudo-tickers). For real symbols, four free research
    sites are linked in the order Aaron requested: Yahoo, TradingView,
    Finviz, SeekingAlpha. All open in a new tab with ``rel='noopener'`` so
    clicking can't hijack the dashboard's session.
    """
    if not ticker or ticker.upper() in {"CASH", "PORTFOLIO"} or "/" in ticker:
        return ""
    t = ticker.upper()
    links = [
        ("Yahoo",        f"https://finance.yahoo.com/quote/{t}"),
        ("TradingView",  f"https://www.tradingview.com/symbols/{t}/"),
        ("Finviz",       f"https://finviz.com/quote.ashx?t={t}"),
        ("SeekingAlpha", f"https://seekingalpha.com/symbol/{t}"),
    ]
    anchors = "".join(
        f"<a href='{href}' target='_blank' rel='noopener noreferrer' "
        f"class='muted ticker-link' "
        f"style='font-size:10px;text-decoration:underline dotted;color:var(--text-secondary)'>"
        f"{label}</a>"
        for label, href in links
    )
    return (
        f"<div class='ticker-links' "
        f"style='display:flex;gap:8px;margin-top:2px'>"
        f"{anchors}</div>"
    )


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


def _portfolio_day_change(portfolio: Portfolio, quotes: dict) -> float | None:
    """Weighted average day-change of equity positions (cash excluded).

    Returns ``None`` when no positions have a usable change_pct.
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for pos in portfolio.positions:
        if pos.is_cash:
            continue
        q = quotes.get(pos.ticker)
        if not q or q.change_pct is None:
            continue
        total_weight += pos.current_weight_pct
        weighted_sum += pos.current_weight_pct * q.change_pct
    if total_weight == 0:
        return None
    return weighted_sum / total_weight


def _kpi_tile(label: str, value: str, delta: float | None, footer: str = "") -> str:
    """Render one KPI tile matching the institutional design language."""
    if delta is None:
        delta_html = "<div class='num' style='font-size:0.86rem;color:var(--subtle)'>—</div>"
    else:
        cls = "pos" if delta >= 0 else "neg"
        sign = "+" if delta >= 0 else ""
        delta_html = (
            f"<div class='num kpi-delta {cls}' style='font-size:0.92rem;font-weight:600'>"
            f"{sign}{delta:.2f}%</div>"
        )
    foot = f"<div class='tag-sourced' style='margin-top:2px'>{footer}</div>" if footer else ""
    return (
        f"<div class='card kpi-tile' style='margin:0;padding:10px 14px'>"
        f"<div class='tag-sourced'>{label}</div>"
        f"<div class='num' style='font-size:1.3rem;color:var(--ink);font-weight:600'>{value}</div>"
        f"{delta_html}{foot}"
        f"</div>"
    )


def page_dashboard() -> None:
    portfolio = _load_portfolio()
    ips = _load_ips()
    benchmarks = _load_benchmarks_all()
    triggers = _load_triggers()

    bench_tickers = [benchmarks["primary"]["ticker"]] + [b["ticker"] for b in benchmarks["reference"]]
    eq_tickers = tuple(p.ticker for p in portfolio.equity_positions if p.ticker not in {"CASH"})
    quotes = _load_quotes(eq_tickers + tuple(bench_tickers))

    snapshot_meta = repo_snapshot_age()
    if snapshot_meta is not None:
        gen, _age = snapshot_meta
        refreshed_iso = gen.replace(tzinfo=__import__("datetime").timezone.utc).isoformat()
    else:
        refreshed_iso = ""

    # ---- Header: title + live ET clock + last-refresh stamp ----
    st.markdown(
        "<h1>WM Growth Portfolio</h1>"
        "<div id='wm-clocks' class='tag-sourced'>"
        "Data refreshed: <span id='wm-asof' class='num'>"
        + (gen.strftime("%Y-%m-%d %H:%M") + " UTC" if snapshot_meta else "—")
        + "</span>"
        "<span style='margin:0 10px;color:var(--rule)'>|</span>"
        "Now: <span id='wm-now' class='num'>—</span> ET"
        "</div>",
        unsafe_allow_html=True,
    )
    # Inject the JS that ticks the ET clock once a second.
    st.components.v1.html(
        f"""
        <script>
        (function () {{
          const fmt = new Intl.DateTimeFormat("en-US", {{
            timeZone: "America/New_York", hour12: false,
            year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit", second: "2-digit"
          }});
          function tick() {{
            // Streamlit renders components inside iframes; reach up to parent
            // document to find the clock span we just emitted.
            const doc = window.parent ? window.parent.document : document;
            const el = doc.getElementById("wm-now");
            if (el) el.textContent = fmt.format(new Date());
            const asof = doc.getElementById("wm-asof");
            // Also format the snapshot time in ET if we have an ISO timestamp.
            const iso = "{refreshed_iso}";
            if (asof && iso) {{
              asof.textContent = fmt.format(new Date(iso)) + " ET";
            }}
          }}
          tick(); setInterval(tick, 1000);
        }})();
        </script>
        """,
        height=0,
    )

    # ---- 4-up benchmark KPI strip: Portfolio + 3 indices ----
    port_delta = _portfolio_day_change(portfolio, quotes)
    weight_total = portfolio.total_weight_pct
    weight_footer = (
        "reconciles to 100%" if abs(weight_total - 100.0) < 0.01 else f"off by {weight_total - 100.0:+.2f}%"
    )

    tiles = [_kpi_tile(
        "Portfolio (day)",
        f"{port_delta:+.2f}%" if port_delta is not None else "—",
        None,  # value already shows day-change
        f"{len(portfolio.equity_positions)} positions · cash {portfolio.cash_pct:.1f}% · {weight_footer}",
    )]
    for spec in [benchmarks["primary"]] + benchmarks["reference"]:
        q = quotes.get(spec["ticker"])
        price = f"{q.price:,.2f}" if q and q.price else "—"
        delta = q.change_pct if q else None
        prov = q.source if q else "stale"
        tiles.append(_kpi_tile(spec["label"], price, delta, prov.upper()))

    st.markdown(
        "<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0 6px 0'>"
        + "".join(tiles)
        + "</div>",
        unsafe_allow_html=True,
    )

    # ---- Compact secondary strip: IPS breach count ----
    breach_count = sum(1 for b in check_portfolio(portfolio, ips) if b.is_breach)
    breach_color = "var(--breach, #A23A2E)" if breach_count else "var(--ok, #2F6B3D)"
    st.markdown(
        f"<div class='tag-sourced' style='margin:0 0 18px 0'>"
        f"IPS v{ips.version if hasattr(ips, 'version') else '1.1'} · "
        f"<span style='color:{breach_color};font-weight:700'>{breach_count} breach{'es' if breach_count != 1 else ''}</span>"
        f" — see IPS Status panel below"
        f"</div>",
        unsafe_allow_html=True,
    )

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
        ticker_cell = (
            f"<strong>{r['Ticker']}</strong>{_ticker_links_html(r['Ticker'])}"
        )
        table_rows.append(
            f"<tr class='{cls}'>"
            f"<td>{ticker_cell}</td>"
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
        st.markdown("### Armed Triggers")
        from datetime import date as _date

        active = [t for t in triggers if t.get("status", "ARMED") == "ARMED"]
        if not active:
            st.markdown("_No armed triggers in `config/triggers.yaml`._")
        today = _date.today()
        rows: list[str] = []
        for t in active:
            ticker = str(t.get("ticker") or "?").upper()
            description = t.get("description", "")
            confidence = t.get("confidence", "—")
            review_by_raw = t.get("review_by")
            review_by = (
                review_by_raw.isoformat() if isinstance(review_by_raw, _date)
                else (review_by_raw or "—")
            )
            stale = False
            if isinstance(review_by_raw, _date):
                stale = review_by_raw < today
            elif isinstance(review_by_raw, str):
                try:
                    stale = _date.fromisoformat(review_by_raw).__lt__(today)
                except ValueError:
                    pass

            # Distance-from-threshold (only for machine-evaluable triggers)
            op = t.get("operator")
            val = t.get("value")
            manual = bool(t.get("manual"))
            last = None
            distance_pct: float | None = None
            if not manual and op and val is not None and ticker in quotes:
                q = quotes.get(ticker)
                last = q.price if q else None
                if last is not None and val:
                    distance_pct = (last - float(val)) / float(val) * 100

            # Render
            distance_html = "—"
            distance_cls = ""
            if distance_pct is not None:
                near = abs(distance_pct) < 5.0
                distance_cls = "neg" if near else "muted"
                distance_html = f"{distance_pct:+.2f}%"
            elif manual:
                distance_html = "<span class='muted'>manual</span>"

            last_html = f"{last:,.2f}" if last is not None else "—"
            rule_html = (
                f"<span class='num'>{op} {val}</span>" if (op and val is not None)
                else "<span class='muted'>—</span>"
            )
            stale_tag = " <span class='tag-stale'>past due</span>" if stale else ""

            rows.append(
                f"<tr>"
                f"<td><strong>{ticker}</strong>{stale_tag}{_ticker_links_html(ticker)}</td>"
                f"<td>{rule_html}</td>"
                f"<td class='num'>{last_html}</td>"
                f"<td class='num {distance_cls}'>{distance_html}</td>"
                f"<td class='muted' style='font-size:0.78rem'>{confidence} · by {review_by}</td>"
                f"</tr>"
                f"<tr><td colspan='5' class='muted' style='font-size:0.80rem;border-bottom:1px solid var(--rule);padding-bottom:8px'>"
                f"{description}"
                f"</td></tr>"
            )
        if rows:
            st.markdown(
                "<table style='font-size:0.86rem'>"
                "<thead><tr>"
                "<th>Ticker</th><th>Rule</th><th>Last</th><th>Distance</th><th>Conf · Review</th>"
                "</tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table>",
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
    benchmarks = _load_benchmarks_all()

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
    # Reference indices to overlay alongside the primary benchmark.
    reference_specs = list(benchmarks.get("reference") or [])
    reference_tickers = tuple(b["ticker"] for b in reference_specs)

    with st.spinner("Loading prices..."):
        prices = _load_history(tickers + (bench_ticker,) + reference_tickers, str(start), str(end))

    if prices.empty:
        st.error("No price data returned. Check yfinance connectivity.")
        return

    bench_rets = prices[bench_ticker].pct_change().dropna() if bench_ticker in prices.columns else pd.Series(dtype=float)
    port_rets = portfolio_returns(
        prices.drop(columns=[bench_ticker, *reference_tickers], errors="ignore"),
        weights,
    )

    if port_rets.empty or bench_rets.empty:
        st.error("Insufficient overlapping data between portfolio and benchmark.")
        return

    # Risk metrics use the primary IPS benchmark (S&P 500 TR). Reference
    # indices are visual overlays only.
    snap = snapshot(port_rets, bench_rets)
    curves = compute_curves(port_rets, bench_rets)

    # Per-series line styling — distinct slate/blue/teal hues so every
    # series is identifiable without relying on dash patterns. Portfolio
    # is heaviest; references are slightly thinner for visual hierarchy.
    line_styles: dict[str, dict] = {
        "Portfolio":         {"color": "#0f172a", "width": 2.25},  # slate-900
        bench_cfg["label"]:  {"color": "#64748b", "width": 1.75},  # slate-500
        "Nasdaq Composite":  {"color": "#1d4ed8", "width": 1.75},  # blue-700
        "Nasdaq 100":        {"color": "#0d9488", "width": 1.75},  # teal-600
    }

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=curves.index, y=curves["Portfolio"], name="Portfolio", mode="lines",
            line=line_styles["Portfolio"],
        )
    )
    fig.add_trace(
        go.Scatter(
            x=curves.index, y=curves["Benchmark"], name=bench_cfg["label"], mode="lines",
            line=line_styles.get(bench_cfg["label"], {"color": "#64748b", "width": 1.75}),
        )
    )
    # Overlay each reference index as its own normalized cumulative-growth line,
    # rebased to 1.0 at the chart start so they're directly comparable.
    for spec in reference_specs:
        rt = spec["ticker"]
        if rt not in prices.columns:
            continue
        rets = prices[rt].pct_change().dropna()
        if rets.empty:
            continue
        # Align to the same date range used in `curves` so the lines start
        # together; reindex on the curves index, forward-filling the gaps.
        ref_curve = (1.0 + rets).cumprod().reindex(curves.index, method="pad")
        label = spec.get("label", rt)
        fig.add_trace(
            go.Scatter(
                x=ref_curve.index,
                y=ref_curve.values,
                name=label,
                mode="lines",
                line=line_styles.get(label, {"color": "#94a3b8", "width": 1.5}),
            )
        )
    layout = style.grayscale_layout(title=f"Cumulative growth · {snap.n_observations} obs")
    # Override the default margin / legend / modebar with the spec layout.
    layout["margin"] = {"l": 48, "r": 24, "t": 56, "b": 40}
    layout["legend"] = {
        "orientation": "h",
        "y": 1.12,
        "x": 0,
        "xanchor": "left",
        "font": {"color": style.SUBTLE, "size": 11},
    }
    layout["modebar"] = {"orientation": "v"}
    fig.update_layout(**layout)
    # Note: we *don't* call style.style_lines here — it would overwrite our
    # per-series colors with the grayscale shade ramp.
    st.plotly_chart(
        fig,
        width="stretch",
        config={
            "displaylogo": False,
            "modeBarButtonsToRemove": [
                "lasso2d", "select2d", "autoScale2d", "toggleSpikelines",
            ],
            "responsive": True,
        },
    )

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


SESSION_TTL_HOURS = 12
LOCKOUT_AFTER_FAILURES = 5
LOCKOUT_DURATION_MINUTES = 15
MIN_PASSWORD_LENGTH = 12


def _get_secret_password() -> str | None:
    """Read the dashboard password from Streamlit secrets.

    Returns ``None`` when no secret is configured (local-dev open-access mode).
    Logs (to stdout, server-side only) a warning if the configured password is
    weak; the warning is not surfaced to users.
    """
    try:
        expected = st.secrets["app_password"]
    except (FileNotFoundError, KeyError):
        return None
    if not expected:
        return None
    expected = str(expected)
    if len(expected) < MIN_PASSWORD_LENGTH:
        logging.warning(
            "Configured app_password is %d chars (< %d recommended). Update via "
            "Streamlit Cloud Settings -> Secrets.",
            len(expected),
            MIN_PASSWORD_LENGTH,
        )
    return expected


def _is_locked_out() -> tuple[bool, int]:
    """Return ``(locked, seconds_remaining)``. Per-session rate limit."""
    locked_until = st.session_state.get("locked_until")
    if not locked_until:
        return False, 0
    remaining = int((locked_until - datetime.now()).total_seconds())
    if remaining <= 0:
        st.session_state.pop("locked_until", None)
        st.session_state["fail_count"] = 0
        return False, 0
    return True, remaining


def _record_failure() -> None:
    """Increment the failed-attempt counter and lock the session if at limit.

    The lockout is per-browser-session; an attacker who opens a fresh tab can
    reset their counter. Genuine brute-force protection requires a strong
    password (>= 12 chars, mixed alphabet) — see the README. The lockout is a
    speed bump, not a wall.
    """
    fails = int(st.session_state.get("fail_count", 0)) + 1
    st.session_state["fail_count"] = fails
    if fails >= LOCKOUT_AFTER_FAILURES:
        st.session_state["locked_until"] = datetime.now() + timedelta(
            minutes=LOCKOUT_DURATION_MINUTES
        )
        logging.warning(
            "Auth: %d failed attempts in this session — locked out for %d minutes.",
            fails,
            LOCKOUT_DURATION_MINUTES,
        )
    else:
        logging.warning("Auth: failed login attempt (%d/%d).", fails, LOCKOUT_AFTER_FAILURES)


def _session_expired() -> bool:
    """Force re-auth after SESSION_TTL_HOURS even if the tab stays open."""
    authed_at = st.session_state.get("authed_at")
    if not authed_at:
        return False
    return (datetime.now() - authed_at).total_seconds() > SESSION_TTL_HOURS * 3600


def _check_password() -> bool:
    """Auth gate. Returns True only if the user is authenticated.

    Security posture:
    - Constant-time password comparison (``hmac.compare_digest``).
    - Per-session rate limit: ``LOCKOUT_AFTER_FAILURES`` failed attempts in
      the same browser session triggers a ``LOCKOUT_DURATION_MINUTES`` lockout.
    - Session TTL: a successful login is good for ``SESSION_TTL_HOURS``; after
      that the user must re-enter the password even if the tab is still open.
    - Generic error messaging: wrong-password and locked-out states never
      reveal whether a password is set or not.
    - The configured password is stored in Streamlit Cloud's encrypted secrets
      store; it never enters the public repo.
    - All transport is HTTPS (Streamlit Cloud terminates TLS).

    Limits to be honest about:
    - This is a single shared password — there are no individual user accounts.
    - Streamlit Cloud staff can technically read your secrets store. For higher
      assurance, run on a private deploy (Streamlit for Teams, self-host, or
      put a Cloudflare Access proxy in front of the public URL).
    - The session lockout is per-browser-session; a determined attacker can
      reset by opening a fresh tab. Choose a long, high-entropy password.
    """
    expected = _get_secret_password()
    if expected is None:
        return True

    if st.session_state.get("authed") and not _session_expired():
        return True
    if _session_expired():
        st.session_state.pop("authed", None)
        st.session_state.pop("authed_at", None)

    locked, remaining = _is_locked_out()

    st.markdown(style.CSS, unsafe_allow_html=True)
    st.markdown(
        "<div style='max-width:420px;margin:6vh auto 0 auto'>"
        "<h1 style='margin-bottom:0.25rem'>WM Growth Portfolio</h1>"
        "<div class='tag-sourced' style='margin-bottom:1.5rem'>"
        "Restricted access. Enter the dashboard password to continue."
        "</div></div>",
        unsafe_allow_html=True,
    )

    if locked:
        mins = remaining // 60
        secs = remaining % 60
        st.markdown(
            "<div style='max-width:420px;margin:0 auto'>"
            f"<div class='card card-breach'>{style.status_pill('BREACH')} "
            f"Too many failed attempts. Try again in {mins}m {secs:02d}s."
            "</div></div>",
            unsafe_allow_html=True,
        )
        st.stop()
        return False

    with st.form("login_form", clear_on_submit=True):
        pw = st.text_input(
            "Password",
            type="password",
            label_visibility="collapsed",
            placeholder="Password",
        )
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if hmac.compare_digest((pw or "").strip(), expected):
            st.session_state["authed"] = True
            st.session_state["authed_at"] = datetime.now()
            st.session_state["fail_count"] = 0
            st.rerun()
        else:
            _record_failure()
            st.markdown(
                "<div style='max-width:420px;margin:0 auto'>"
                f"<div class='card card-breach'>{style.status_pill('BREACH')} "
                "Incorrect password."
                "</div></div>",
                unsafe_allow_html=True,
            )
    st.stop()
    return False  # unreachable, kept for mypy


def _render_signout_in_sidebar() -> None:
    if st.session_state.get("authed"):
        with st.sidebar:
            st.markdown(
                "<div class='tag-sourced' style='margin-top:1rem'>Authenticated</div>",
                unsafe_allow_html=True,
            )
            if st.button("Sign out", width="stretch"):
                st.session_state.pop("authed", None)
                st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="WM Growth Portfolio",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if not _check_password():
        return
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
    _render_signout_in_sidebar()


main()
