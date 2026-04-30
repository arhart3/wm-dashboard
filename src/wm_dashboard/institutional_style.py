"""Grayscale institutional style palette and Plotly helpers.

Mirrors the report templates: Calibri prose, Consolas for numbers, single-rule
tables with subtle alternating rows. The palette below is the source of truth
for both the printed reports and the Streamlit dashboard.
"""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

INK = "#111111"
SUBTLE = "#5A5A5A"
RULE = "#CFCFCF"
HEADER_BG = "#ECECEC"
ALT_BG = "#F6F6F6"
PAPER_BG = "#FFFFFF"
ACCENT_REVIEW = "#9A6A00"
ACCENT_BREACH = "#A23A2E"
ACCENT_OK = "#2F6B3D"

PALETTE: dict[str, str] = {
    "ink": INK,
    "subtle": SUBTLE,
    "rule": RULE,
    "header_bg": HEADER_BG,
    "alt_bg": ALT_BG,
    "paper": PAPER_BG,
    "ok": ACCENT_OK,
    "review": ACCENT_REVIEW,
    "breach": ACCENT_BREACH,
}

PROSE_FONT = "Calibri, Helvetica, Arial, sans-serif"
NUMERIC_FONT = "Consolas, 'SF Mono', Menlo, monospace"


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
    --ink: {INK};
    --subtle: {SUBTLE};
    --rule: {RULE};
    --header-bg: {HEADER_BG};
    --alt-bg: {ALT_BG};
    --paper: {PAPER_BG};
    --ok: {ACCENT_OK};
    --review: {ACCENT_REVIEW};
    --breach: {ACCENT_BREACH};
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

/* Pills — compact rectangles, not pills. */
.pill {{
    display: inline-block;
    padding: 1px 8px;
    border-radius: 2px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border: 1px solid transparent;
    vertical-align: middle;
}}
.pill-ok     {{ background: #ECF1ED; color: {ACCENT_OK}    !important; border-color: #C8D6CC; }}
.pill-review {{ background: #F4EEDD; color: {ACCENT_REVIEW} !important; border-color: #D9C99A; }}
.pill-breach {{ background: #F1DFDC; color: {ACCENT_BREACH} !important; border-color: #D9B6B0; }}

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
