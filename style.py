"""Shared JSA styling for the WASDE dashboard — COT-theme variant."""

import base64
from pathlib import Path

import streamlit as st

# ── Palette (matches CFTC COT dashboard) ─────────────────────────────────────
JSA_GREEN    = "#5e7164"
BG           = "#f6f8f7"
SURFACE      = "#ffffff"
BORDER       = "#d7e2dc"
TEXT         = "#32373c"
TEXT_MUTED   = "#5f7267"

# Chart accent colors
C_GREEN  = "#5e7164"
C_LIME   = "#8db89a"
C_GOLD   = "#b8972a"
C_BLUE   = "#6fa8c4"
C_ORANGE = "#c4785a"
C_RED    = "#dc2626"
C_PURPLE = "#9b89c4"

SERIES_COLORS = [C_BLUE, C_ORANGE, C_GOLD, C_GREEN, C_PURPLE, C_RED, C_LIME]

JSA_LOGO_FULL = "https://www.jpsi.com/wp-content/themes/gate39media/img/logo-full.png"

HERE = Path(__file__).parent
_WATERMARK_PATH = HERE / "assets" / "logo-50yr.png"
WATERMARK_OPACITY = 0.08


@st.cache_data(show_spinner=False)
def watermark_uri() -> str | None:
    try:
        return "data:image/png;base64," + base64.b64encode(
            _WATERMARK_PATH.read_bytes()
        ).decode()
    except OSError:
        return None


# ── Plotly layout base ────────────────────────────────────────────────────────
_AXIS = dict(
    gridcolor=BORDER, linecolor=BORDER, showgrid=True,
    tickfont=dict(color=TEXT_MUTED, size=11),
    title_font=dict(color=TEXT_MUTED, size=11),
    zeroline=False,
)


def plot_layout(**overrides) -> dict:
    wm = watermark_uri()
    base = dict(
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(color=TEXT, family="Source Sans 3, Source Sans Pro, sans-serif", size=12),
        margin=dict(l=10, r=10, t=40, b=55),
        xaxis=dict(**_AXIS),
        yaxis=dict(**_AXIS),
        legend=dict(
            bgcolor="rgba(0,0,0,0)", font=dict(size=11, color=TEXT_MUTED),
            orientation="h", yanchor="bottom", y=-0.22, xanchor="center", x=0.5,
        ),
        hovermode="x unified",
    )
    if wm:
        base["images"] = [dict(
            source=wm, xref="paper", yref="paper",
            x=0.5, y=0.5, sizex=0.45, sizey=0.45,
            xanchor="center", yanchor="middle",
            sizing="contain", opacity=WATERMARK_OPACITY, layer="below",
        )]
    base.update(overrides)
    return base


# ── Global CSS ────────────────────────────────────────────────────────────────
CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=EB+Garamond:wght@500;600&family=Source+Sans+3:wght@400;600;700&display=swap');

html, body, [data-testid="stAppViewContainer"] {{
    background-color: {BG};
    color: {TEXT};
}}
html, body, p, span, div, label, button, td, th {{
    font-family: 'Source Sans 3', 'Source Sans Pro', sans-serif;
}}
.block-container {{ padding-top: 2rem; max-width: 1400px; }}
#MainMenu, footer, .stDeployButton {{ visibility: hidden; }}
[data-testid="stSidebar"] {{ display: none !important; }}
[data-testid="collapsedControl"] {{ display: none !important; }}

/* ── Top navigation bar ── */
.top-nav {{
    display: flex; gap: 4px; align-items: center;
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 12px; margin-bottom: 16px; overflow-x: auto;
}}
.top-nav a {{
    color: {TEXT_MUTED}; text-decoration: none; font-size: 0.85rem;
    font-weight: 500; padding: 5px 12px; border-radius: 6px; white-space: nowrap;
    transition: background 0.15s, color 0.15s;
}}
.top-nav a:hover {{ background: {BG}; color: {TEXT}; }}
.top-nav a[aria-current="page"],
.top-nav a.active {{
    background: {JSA_GREEN}; color: #fff !important; font-weight: 600;
}}
.top-nav .nav-sep {{ color: {BORDER}; padding: 0 2px; user-select: none; }}

/* ── Header ── */
.dash-header {{
    display: flex; align-items: center; gap: 18px;
    padding-bottom: 10px; border-bottom: 1px solid {BORDER}; margin-bottom: 14px;
}}
.dash-header img {{ height: 38px; }}
.dash-header .t b {{
    font-family: 'EB Garamond', Georgia, serif;
    font-size: 1.65rem; color: {TEXT}; font-weight: 600;
}}
.dash-header .t div {{ font-size: .84rem; color: {TEXT_MUTED}; }}
@media (max-width: 640px) {{ .dash-header img {{ display: none; }} }}

/* ── Control bar ── */
.ctrl-bar {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 10px 16px; margin-bottom: 16px;
}}

/* ── Stat tiles ── */
.tile {{
    background: {SURFACE}; border: 1px solid {BORDER};
    border-top: 3px solid {JSA_GREEN}; border-radius: 10px;
    padding: 14px 18px; text-align: center; height: 100%;
}}
.tile-label {{
    color: {TEXT_MUTED}; font-size: 0.68rem; text-transform: uppercase;
    letter-spacing: 0.09em; margin-bottom: 6px;
}}
.tile-value {{ color: {TEXT}; font-size: 1.45rem; font-weight: 700; line-height: 1.1; }}
.tile-sub {{ color: {TEXT_MUTED}; font-size: 0.72rem; margin-top: 5px; }}
.tile-delta {{ color: {TEXT_MUTED}; font-size: 0.82rem; font-weight: 600; margin-top: 4px; }}

/* ── Streamlit metric override ── */
[data-testid="stMetric"] {{
    background: {SURFACE}; border: 1px solid {BORDER};
    border-top: 3px solid {JSA_GREEN}; border-radius: 10px;
    padding: 14px 18px;
}}
[data-testid="stMetricLabel"] {{
    color: {TEXT_MUTED} !important; font-size: 0.68rem !important;
    text-transform: uppercase; letter-spacing: 0.09em; font-weight: 600;
}}
[data-testid="stMetricValue"] {{
    color: {TEXT} !important; font-size: 1.4rem !important; font-weight: 700;
}}
[data-testid="stMetricDelta"] {{ font-size: 0.78rem !important; }}
[data-testid="stMetricDeltaIcon-Up"]   {{ color: #16a34a !important; }}
[data-testid="stMetricDeltaIcon-Down"] {{ color: {C_RED} !important; }}

/* ── Section headers ── */
.sec-header {{
    color: {TEXT_MUTED}; font-size: 0.7rem; text-transform: uppercase;
    letter-spacing: 0.1em; padding: 10px 0 4px;
    border-bottom: 1px solid {BORDER}; margin-bottom: 12px;
}}

/* ── DataFrames ── */
.stDataFrame {{ border: 1px solid {BORDER} !important; border-radius: 8px; overflow: hidden; }}

/* ── Selectboxes ── */
div[data-testid="stSelectbox"] > div,
div[data-testid="stMultiSelect"] > div {{
    border-color: {BORDER}; border-radius: 6px;
}}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {{
    background: {SURFACE}; border-bottom: 2px solid {BORDER}; gap: 2px; padding: 0 4px;
}}
.stTabs [data-baseweb="tab"] {{
    color: {TEXT_MUTED}; background: transparent;
    border-radius: 6px 6px 0 0; padding: 10px 20px;
    font-weight: 500; font-size: 0.88rem;
}}
.stTabs [aria-selected="true"] {{
    color: {JSA_GREEN} !important; background: {BG} !important;
    border-bottom: 2px solid {JSA_GREEN} !important; font-weight: 700;
}}

hr {{ border-color: {BORDER}; }}
</style>
"""

HEADER_HTML = f"""
<div class="dash-header">
  <img src="{JSA_LOGO_FULL}" alt="JSA">
  <div class="t">
    <b>WASDE Dashboard</b>
    <div>World Ag Supply &amp; Demand &middot; John Stewart &amp; Associates</div>
  </div>
</div>
"""


def inject():
    """Inject shared CSS + header. Call once per page."""
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(HEADER_HTML, unsafe_allow_html=True)


def sec(label: str):
    st.markdown(f"<div class='sec-header'>{label}</div>", unsafe_allow_html=True)


_NAV_PAGES = [
    ("app",                    "🌾 Balance Sheet"),
    ("pages/2_Progression",    "📈 Progression"),
    ("pages/3_Bucket_Trends",  "📊 Bucket Trends"),
    ("pages/4_Countries",      "🌍 Countries"),
    ("pages/5_Tendencies",     "🔍 Tendencies"),
    ("pages/7_Origins",        "🌎 Origins"),
    ("pages/6_Ask_AI",         "🤖 Ask AI"),
]


def nav_bar():
    """Render horizontal page-link nav bar. Call after inject()."""
    cols = st.columns(len(_NAV_PAGES))
    for col, (path, label) in zip(cols, _NAV_PAGES):
        with col:
            st.page_link(f"{path}.py", label=label, use_container_width=True)


def disclaimer():
    """Render the standard JSA disclaimer footer."""
    st.markdown(
        f"<div style='text-align:center;color:{TEXT_MUTED};font-size:0.72rem;"
        f"margin-top:32px;padding-top:12px;border-top:1px solid {BORDER}'>"
        "Data sourced from USDA Foreign Agricultural Service — PSD Online. "
        "This dashboard is for informational purposes only and does not constitute "
        "investment or trading advice. &nbsp;·&nbsp; "
        "&copy; John Stewart &amp; Associates"
        "</div>",
        unsafe_allow_html=True,
    )


COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]


def top_nav(commodity_options: list[str] | None = None,
            show_region: bool = True,
            show_units: bool = True,
            default_commodity: str = "Corn") -> tuple:
    """
    Render the top control bar and return (commodity, region, imperial).
    Wraps controls in a styled div; uses st.columns for a compact horizontal layout.
    """
    if commodity_options is None:
        commodity_options = COMMODITIES

    st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)

    n_cols = 1 + int(show_region) + int(show_units)
    cols = st.columns([2] + [1] * (n_cols - 1))

    with cols[0]:
        commodity = st.selectbox(
            "Commodity", commodity_options,
            index=commodity_options.index(default_commodity)
            if default_commodity in commodity_options else 0,
            label_visibility="collapsed",
        )

    region = "US"
    imperial = False
    col_i = 1

    if show_region:
        with cols[col_i]:
            region = st.radio("Region", ["US", "World"], horizontal=True,
                              label_visibility="collapsed")
        col_i += 1

    if show_units:
        with cols[col_i]:
            imperial = st.toggle("Million bu", value=False)

    st.markdown("</div>", unsafe_allow_html=True)
    return commodity, region, imperial


# Keep backward-compat alias (pages that still call sidebar_nav will get top_nav)
def sidebar_nav(commodity_options: list[str] | None = None,
                default_commodity: str = "Corn") -> tuple:
    return top_nav(commodity_options, default_commodity=default_commodity)
