"""Shared JSA styling for the WASDE dashboard."""

import streamlit as st

# ── Colors ───────────────────────────────────────────────────────────────────
JSA_GREEN    = "#5e7164"
JSA_GREEN_LT = "#8db89a"
JSA_WHITE    = "#ffffff"

# Neutral surface palette (light, clean — River FOB Portal aesthetic)
BG          = "#f7f8f7"
SURFACE     = "#ffffff"
SURFACE2    = "#f0f4f1"
BORDER      = "#dde5de"
TEXT        = "#1a2420"
TEXT_MUTED  = "#6b7f72"

# Chart accent colors
C_GREEN  = "#5e7164"
C_LIME   = "#8db89a"
C_GOLD   = "#b8972a"
C_BLUE   = "#3a7ca5"
C_ORANGE = "#c07040"
C_RED    = "#c05050"
C_PURPLE = "#7060a0"

SERIES_COLORS = [C_GREEN, C_BLUE, C_GOLD, C_ORANGE, C_PURPLE, C_RED, C_LIME]

JSA_LOGO = "https://www.jpsi.com/wp-content/themes/gate39media/img/logo-white.png"

# ── Plotly layout base ────────────────────────────────────────────────────────
PLOT_BASE = dict(
    paper_bgcolor = SURFACE,
    plot_bgcolor  = SURFACE2,
    font          = dict(color=TEXT, family="Inter, -apple-system, sans-serif", size=12),
    margin        = dict(l=8, r=8, t=36, b=8),
    xaxis         = dict(gridcolor=BORDER, zeroline=False, tickfont=dict(size=11)),
    yaxis         = dict(gridcolor=BORDER, zeroline=False, tickfont=dict(size=11)),
    legend        = dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11), orientation="h",
                         yanchor="bottom", y=1.02, xanchor="left", x=0),
    hovermode     = "x unified",
)


def plot_layout(**overrides) -> dict:
    base = {**PLOT_BASE}
    base.update(overrides)
    return base


# ── Global CSS ────────────────────────────────────────────────────────────────
CSS = f"""
<style>
/* ── Reset Streamlit defaults ── */
html, body, [data-testid="stAppViewContainer"] {{
    background: {BG};
    color: {TEXT};
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
#MainMenu, footer, header {{ visibility: hidden; }}

/* ── Sidebar ── */
[data-testid="stSidebar"] {{
    background: {JSA_GREEN};
    border-right: none;
}}
[data-testid="stSidebar"] * {{
    color: {JSA_WHITE} !important;
}}
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div,
[data-testid="stSidebar"] [data-testid="stMultiSelect"] > div {{
    background: rgba(255,255,255,0.12) !important;
    border-color: rgba(255,255,255,0.25) !important;
    color: {JSA_WHITE} !important;
}}
[data-testid="stSidebar"] label {{
    color: rgba(255,255,255,0.75) !important;
    font-size: 0.75rem !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}}
[data-testid="stSidebar"] hr {{
    border-color: rgba(255,255,255,0.2);
}}
[data-testid="stSidebar"] [data-baseweb="select"] svg {{
    fill: {JSA_WHITE} !important;
}}

/* ── Metric cards ── */
[data-testid="stMetric"] {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 14px 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}}
[data-testid="stMetricLabel"] {{
    color: {TEXT_MUTED} !important;
    font-size: 0.72rem !important;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    font-weight: 600;
}}
[data-testid="stMetricValue"] {{
    color: {TEXT} !important;
    font-size: 1.4rem !important;
    font-weight: 700;
}}
[data-testid="stMetricDelta"] {{ font-size: 0.78rem !important; }}
[data-testid="stMetricDeltaIcon-Up"]   {{ color: {C_GREEN} !important; }}
[data-testid="stMetricDeltaIcon-Down"] {{ color: {C_RED} !important; }}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {{
    background: {SURFACE};
    border-bottom: 2px solid {BORDER};
    gap: 2px;
    padding: 0 4px;
}}
.stTabs [data-baseweb="tab"] {{
    color: {TEXT_MUTED};
    background: transparent;
    border-radius: 6px 6px 0 0;
    padding: 10px 20px;
    font-weight: 500;
    font-size: 0.88rem;
}}
.stTabs [aria-selected="true"] {{
    color: {JSA_GREEN} !important;
    background: {SURFACE2} !important;
    border-bottom: 2px solid {JSA_GREEN} !important;
    font-weight: 700;
}}

/* ── DataFrames ── */
.stDataFrame {{
    border: 1px solid {BORDER} !important;
    border-radius: 8px;
    overflow: hidden;
}}

/* ── Section labels ── */
.sec-label {{
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: {TEXT_MUTED};
    margin: 20px 0 8px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid {BORDER};
}}

/* ── Info / warning banners ── */
[data-testid="stInfo"] {{
    background: {SURFACE2};
    border-left: 3px solid {JSA_GREEN_LT};
    border-radius: 6px;
}}

/* ── Buttons ── */
[data-testid="stButton"] > button {{
    background: {JSA_GREEN};
    color: {JSA_WHITE};
    border: none;
    border-radius: 6px;
    font-weight: 600;
    padding: 8px 20px;
}}
[data-testid="stButton"] > button:hover {{
    background: {JSA_GREEN_LT};
}}

/* ── Selectboxes (main area) ── */
div[data-testid="stSelectbox"] > div,
div[data-testid="stMultiSelect"] > div {{
    border-color: {BORDER};
    border-radius: 6px;
}}
</style>
"""


def inject():
    """Call once at the top of each page to apply the shared CSS."""
    st.markdown(CSS, unsafe_allow_html=True)


def sec(label: str):
    st.markdown(f"<div class='sec-label'>{label}</div>", unsafe_allow_html=True)


def sidebar_nav(commodity_options: list[str], default_commodity: str = "Corn"):
    """Render the shared sidebar and return selected values."""
    with st.sidebar:
        st.image(JSA_LOGO, width=110)
        st.markdown(
            f"<div style='color:rgba(255,255,255,0.9);font-size:1.05rem;"
            f"font-weight:700;margin:8px 0 4px'>WASDE Dashboard</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div style='color:rgba(255,255,255,0.6);font-size:0.75rem;"
            "margin-bottom:16px'>World Ag Supply & Demand</div>",
            unsafe_allow_html=True,
        )
        st.markdown("---")

        commodity = st.selectbox("Commodity", commodity_options,
                                 index=commodity_options.index(default_commodity)
                                 if default_commodity in commodity_options else 0)

        region = st.radio("Region", ["US", "World"], horizontal=True)

        imperial = st.toggle("Show in million bu", value=False)

        st.markdown("---")
        st.markdown(
            "<div style='color:rgba(255,255,255,0.5);font-size:0.7rem'>"
            "Source: USDA FAS PSD API<br>Updated monthly with WASDE</div>",
            unsafe_allow_html=True,
        )

    return commodity, region, imperial
