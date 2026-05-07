"""Institutional style palette and Plotly helpers.

The palette is the single source of truth for both the printed reports
and the Streamlit dashboard. Colors are slate-based with restrained
indigo accents and Tailwind-inspired sentiment tokens (emerald-700 for
positive, red-700 for negative, amber-700 for warnings, cyan-700 for
informational). Prose is Inter; numerics stay tabular monospace
(JetBrains Mono → Consolas → system fallback) to align decimal points.
"""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

# Surfaces ------------------------------------------------------------------
PAPER_BG = "#FFFFFF"           # bg
SURFACE_1 = "#F8F9FA"          # subtle alt-row + low-emphasis surfaces
SURFACE_2 = "#EEF1F4"          # table headers, deeper emphasis surfaces
RULE = "#E5E7EB"               # default border / hairline
RULE_STRONG = "#CBD2DA"        # primary table top/bottom rules

# Text ----------------------------------------------------------------------
INK = "#0F172A"                # slate-900 — body
SUBTLE = "#475569"             # slate-600 — secondary text + labels
TEXT_MUTED = "#94A3B8"         # slate-400 — captions, low-emphasis hints

# Accents -------------------------------------------------------------------
ACCENT = "#1E3A8A"             # indigo-900 — used sparingly for emphasis
ACCENT_SOFT = "#E0E7FF"

# Sentiment (Tailwind-inspired) ---------------------------------------------
ACCENT_OK = "#047857"           # emerald-700
OK_SOFT = "#D1FAE5"
ACCENT_BREACH = "#B91C1C"       # red-700
BREACH_SOFT = "#FEE2E2"
ACCENT_REVIEW = "#B45309"       # amber-700
REVIEW_SOFT = "#FEF3C7"
ACCENT_INFO = "#0E7490"         # cyan-700

# Backwards-compat aliases (callers still reference these names)
HEADER_BG = SURFACE_2
ALT_BG = SURFACE_1

PALETTE: dict[str, str] = {
    "ink": INK,
    "subtle": SUBTLE,
    "muted": TEXT_MUTED,
    "rule": RULE,
    "rule_strong": RULE_STRONG,
    "header_bg": SURFACE_2,
    "alt_bg": SURFACE_1,
    "paper": PAPER_BG,
    "accent": ACCENT,
    "ok": ACCENT_OK,
    "review": ACCENT_REVIEW,
    "breach": ACCENT_BREACH,
    "info": ACCENT_INFO,
}

PROSE_FONT = "'Inter', 'Calibri', system-ui, -apple-system, 'Helvetica Neue', sans-serif"
NUMERIC_FONT = "'JetBrains Mono', 'Consolas', 'SF Mono', Menlo, ui-monospace, monospace"


def grayscale_layout(title: str | None = None, height: int = 360) -> dict[str, Any]:
    """Plotly layout dict for a grayscale institutional chart."""
    return {
        "title": {"text": title or "", "font": {"family": PROSE_FONT, "color": INK, "size": 14}},
        "paper_bgcolor": PAPER_BG,
        "plot_bgcolor": PAPER_BG,
        "font": {"family": PROSE_FONT, "color": INK, "size": 12},
        "margin": {"l": 56, "r": 24, "t": 48, "b": 48},
        "height": height,
        "xaxis": {
            "showgrid": False,
            "showline": True,
            "linecolor": RULE,
            "ticks": "outside",
            "tickcolor": RULE,
            "color": SUBTLE,
        },
        "yaxis": {
            "showgrid": True,
            "gridcolor": RULE,
            "zeroline": False,
            "showline": True,
            "linecolor": RULE,
            "ticks": "outside",
            "tickcolor": RULE,
            "color": SUBTLE,
        },
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
            "font": {"color": SUBTLE, "size": 11},
        },
    }


def style_lines(fig: go.Figure) -> go.Figure:
    """Restyle every trace in ``fig`` to use the grayscale palette."""
    shades = [INK, SUBTLE, "#888888", "#A8A8A8"]
    for i, trace in enumerate(fig.data):
        color = shades[i % len(shades)]
        trace.update(line={"color": color, "width": 2}, marker={"color": color})
    return fig


CSS = f"""
<style>
:root {{
    /* Surfaces */
    --bg:           {PAPER_BG};
    --paper:        {PAPER_BG};
    --surface-1:    {SURFACE_1};
    --surface-2:    {SURFACE_2};
    --header-bg:    {SURFACE_2};   /* alias for back-compat */
    --alt-bg:       {SURFACE_1};   /* alias for back-compat */
    --rule:         {RULE};
    --rule-strong:  {RULE_STRONG};

    /* Text */
    --text:           {INK};
    --ink:            {INK};       /* alias */
    --text-secondary: {SUBTLE};
    --subtle:         {SUBTLE};    /* alias */
    --text-muted:     {TEXT_MUTED};

    /* Accents */
    --accent:        {ACCENT};
    --accent-soft:   {ACCENT_SOFT};

    /* Sentiment */
    --pos:        {ACCENT_OK};
    --pos-soft:   {OK_SOFT};
    --ok:         {ACCENT_OK};      /* alias */
    --neg:        {ACCENT_BREACH};
    --neg-soft:   {BREACH_SOFT};
    --breach:     {ACCENT_BREACH};  /* alias */
    --warn:       {ACCENT_REVIEW};
    --warn-soft:  {REVIEW_SOFT};
    --review:     {ACCENT_REVIEW};  /* alias */
    --info:       {ACCENT_INFO};
}}

/* Global reset — pin the institutional light palette regardless of the
   user's system theme. !important is intentional: Streamlit's theme tokens
   would otherwise override us inside [data-testid] containers. */
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stHeader"],
[data-testid="stSidebar"],
section.main, .block-container {{
    background-color: var(--paper) !important;
    color: var(--ink) !important;
    font-family: {PROSE_FONT} !important;
}}

[data-testid="stSidebar"] {{
    background-color: #FAFAFA !important;
    border-right: 1px solid var(--rule);
}}

.block-container {{ padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1400px; }}

h1, h2, h3, h4, h5, p, span, label, li, div {{ color: var(--ink); }}
h1 {{ font-size: 1.45rem; font-weight: 600; letter-spacing: 0.02em; margin-bottom: 0.25rem; }}
h2 {{
    text-transform: uppercase;
    font-size: 0.92rem;
    font-weight: 600;
    letter-spacing: 0.10em;
    color: var(--ink);
    border-bottom: 1px solid var(--rule);
    padding-bottom: 6px;
    margin-top: 1.6rem;
    margin-bottom: 0.75rem;
}}
h3 {{
    text-transform: uppercase;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.10em;
    color: var(--subtle);
    margin-top: 1.2rem;
    margin-bottom: 0.4rem;
}}

div[data-testid="stMetricValue"], code, pre, .num {{
    font-family: {NUMERIC_FONT};
    font-variant-numeric: tabular-nums;
}}

/* Directional / sentiment colors — used in KPI deltas, position day %,
   trigger distance-from-threshold, etc. Subtle enough to read on white,
   distinct enough to scan at a glance. */
.pos    {{ color: var(--pos) !important; }}
.neg    {{ color: var(--neg) !important; }}
.warn   {{ color: var(--warn) !important; }}
.info   {{ color: var(--info) !important; }}
.muted  {{ color: var(--text-muted) !important; }}

/* KPI tiles + provenance badges (sourced / confirmed / calibrated) */
.kpi-tile {{ min-height: 96px; }}
.kpi-delta {{ margin-top: 2px; }}

.badge {{
    display: inline-block;
    padding: 1px 6px;
    font-size: 10px;
    border-radius: 2px;
    background: var(--header-bg);
    color: var(--subtle);
    margin-left: 6px;
    vertical-align: 2px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}
.badge.sourced    {{ background: var(--pos-soft);  color: var(--pos); }}
.badge.confirmed  {{ background: var(--surface-2); color: var(--text-secondary); }}
.badge.calibrated {{ background: var(--warn-soft); color: var(--warn); }}

/* Tables — single-rule, alternating rows, tight padding. */
table {{ border-collapse: collapse; width: 100%; font-size: 0.86rem; }}
thead tr th {{
    background: var(--header-bg) !important;
    color: var(--ink) !important;
    text-align: left;
    border-bottom: 1px solid var(--ink) !important;
    border-top: 1px solid var(--ink) !important;
    padding: 6px 10px;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.72rem;
    letter-spacing: 0.06em;
}}
tbody tr td {{
    border-bottom: 1px solid var(--rule);
    padding: 5px 10px;
    color: var(--ink) !important;
    background: var(--paper) !important;
}}
tbody tr:nth-child(even) td {{ background: var(--alt-bg) !important; }}
tbody tr:last-child td {{ border-bottom: 1px solid var(--ink); }}
.num {{ text-align: right; }}

/* Pills — fully rounded, soft fills. Used for IPS severity (.pill-ok /
   -review / -breach) and for general sentiment (.pill.pos / .neg / .warn
   / .info, mirroring Tailwind tokens). */
.pill {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    vertical-align: middle;
}}
.pill.pos,  .pill-ok     {{ background: var(--pos-soft);  color: var(--pos)  !important; }}
.pill.warn, .pill-review {{ background: var(--warn-soft); color: var(--warn) !important; }}
.pill.neg,  .pill-breach {{ background: var(--neg-soft);  color: var(--neg)  !important; }}
.pill.info               {{ background: var(--accent-soft); color: var(--accent) !important; }}

.tag-sourced {{ color: var(--subtle) !important; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 500; }}
.tag-cached  {{ color: var(--subtle) !important; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 500; opacity: 0.7; }}
.tag-stale   {{ color: {ACCENT_BREACH} !important; font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 700; }}

/* Cards — explicit color on every child so dark-mode CSS can't bleed in. */
.card {{
    border: 1px solid var(--rule);
    background: var(--paper) !important;
    padding: 10px 14px;
    margin: 6px 0;
    line-height: 1.35;
}}
.card, .card p, .card span:not(.subtle):not(.tag-sourced):not(.tag-cached):not(.tag-stale), .card strong, .card div:not(.tag-sourced):not(.tag-cached):not(.tag-stale) {{ color: var(--ink) !important; }}
.card .tag-sourced, .card .tag-cached, .card .subtle {{ color: var(--subtle) !important; }}
.card .tag-stale {{ color: {ACCENT_BREACH} !important; }}
.card-ok      {{ border-left: 3px solid {ACCENT_OK}; }}
.card-review  {{ border-left: 3px solid {ACCENT_REVIEW}; background: #FCFAF3 !important; }}
.card-breach  {{ border-left: 3px solid {ACCENT_BREACH}; background: #FBF4F2 !important; }}
.card strong {{ font-weight: 600; }}
.card .num   {{ font-size: 0.82rem; color: var(--subtle) !important; }}

.drift-breach td.drift {{ color: {ACCENT_BREACH} !important; font-weight: 700; }}

/* Streamlit widgets — neutral framing. */
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input {{
    background: var(--paper) !important; color: var(--ink) !important;
    border: 1px solid var(--rule) !important; border-radius: 2px !important;
    font-family: {PROSE_FONT} !important;
}}
.stSelectbox div[data-baseweb="select"] > div {{
    background: var(--paper) !important; color: var(--ink) !important;
    border: 1px solid var(--rule) !important; border-radius: 2px !important;
}}
.stButton button, .stFormSubmitButton button, .stDownloadButton button, .stLinkButton a {{
    background: var(--ink) !important; color: var(--paper) !important;
    border: 1px solid var(--ink) !important; border-radius: 2px !important;
    font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; font-size: 0.78rem;
}}
.stButton button:hover, .stFormSubmitButton button:hover, .stLinkButton a:hover {{
    background: var(--paper) !important; color: var(--ink) !important;
}}

div[data-testid="stMetric"] {{
    border: 1px solid var(--rule); padding: 8px 12px; background: var(--paper) !important;
}}
div[data-testid="stMetricLabel"] {{ color: var(--subtle) !important; text-transform: uppercase; font-size: 0.70rem; letter-spacing: 0.08em; }}
div[data-testid="stMetricValue"] {{ color: var(--ink) !important; font-size: 1.4rem; }}

/* Hide Streamlit's chrome. */
header[data-testid="stHeader"] {{ background: transparent !important; }}
#MainMenu, footer {{ visibility: hidden; }}
</style>
"""


def status_pill(status: str) -> str:
    """Return an HTML pill for a status string ("OK", "REVIEW", "BREACH")."""
    s = status.upper().strip()
    cls = {
        "OK": "pill pill-ok",
        "REVIEW": "pill pill-review",
        "BREACH": "pill pill-breach",
    }.get(s, "pill")
    return f'<span class="{cls}">{s}</span>'


def provenance_tag(source: str) -> str:
    """Return an HTML tag for a price provenance ("SOURCED", "CACHED", "STALE")."""
    s = source.upper().strip()
    cls = {
        "SOURCED": "tag-sourced",
        "CACHED": "tag-cached",
        "STALE": "tag-stale",
    }.get(s, "tag-sourced")
    return f'<span class="{cls}">{s}</span>'
