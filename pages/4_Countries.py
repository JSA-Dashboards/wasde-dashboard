"""
Page 4 — Country View
Top producers, exporters, and stacked country production trends.
"""

import plotly.graph_objects as go
import streamlit as st
import requests

import db
import style
from style import SERIES_COLORS, C_BLUE, C_GREEN, C_LIME
from units import convert, unit_label

st.set_page_config(
    page_title="JSA WASDE — Countries",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="collapsed",
)
style.inject()

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]
COMM_CODES  = {
    "Corn": "0440000", "Soybeans": "2222000", "Wheat": "0410000",
    "Cotton": "5151000", "Rice, Milled": "0422110", "Soybean Oil": "4235000",
}
PSD_BASE = "https://apps.fas.usda.gov/psdonline/api"

st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
_c1, _c2, _c3, _c4, _c5 = st.columns([2, 1, 1, 1, 1])
with _c1:
    commodity = st.selectbox("Commodity", COMMODITIES)
with _c2:
    imperial = st.toggle("Million bu", value=False)
with _c3:
    mkt_year = st.selectbox("Mkt Year", list(range(2026, 2004, -1)), index=0,
                             format_func=lambda y: f"{y}/{str(y+1)[-2:]}")
with _c4:
    attr_choice = st.selectbox("Attribute", [
        "Production", "Exports", "Imports", "Ending Stocks", "Total Use",
    ])
with _c5:
    top_n = st.slider("Top N", 5, 20, 10)
st.markdown("</div>", unsafe_allow_html=True)

ul = unit_label(commodity, imperial)

st.markdown(
    f"<div style='font-family:\"EB Garamond\",Georgia,serif;font-size:1.35rem;"
    f"font-weight:600;color:{style.TEXT};margin-bottom:2px'>"
    f"{commodity} — Country View</div>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<span style='font-size:0.95rem;color:{style.TEXT_MUTED};font-weight:400'>"
    f"Top countries by {attr_choice} &nbsp;·&nbsp; {mkt_year}/{str(mkt_year+1)[-2:]}</span>",
    unsafe_allow_html=True,
)
st.markdown(f"<hr style='margin:6px 0 14px;border-color:{style.BORDER}'>", unsafe_allow_html=True)

ATTR_MAP = {
    "Production": 28, "Exports": 88, "Imports": 40,
    "Ending Stocks": 176, "Total Use": 185,
}

# Use Snowflake if available, else live PSD API
if db.snowflake_available():
    top_df = db.top_countries(commodity, mkt_year, attr_choice, top_n)
else:
    # Live PSD fallback
    @st.cache_data(ttl=3600, show_spinner=False)
    def _world_psd(code):
        try:
            r = requests.get(f"{PSD_BASE}/data/get",
                             params={"commodityCode": code}, timeout=60)
            r.raise_for_status()
            import pandas as pd
            df = pd.DataFrame(r.json() or [])
            return df
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    raw = _world_psd(COMM_CODES[commodity])
    if not raw.empty:
        import pandas as pd
        attr_id = ATTR_MAP[attr_choice]
        top_df = (raw[(raw["attributeId"] == attr_id) & (raw["marketYear"] == mkt_year)]
                  .rename(columns={"countryCode": "country_code",
                                   "countryName": "country_name",
                                   "value": "value"})
                  .nlargest(top_n, "value")[["country_code", "country_name", "value"]])
    else:
        top_df = None

if top_df is None or (hasattr(top_df, "empty") and top_df.empty):
    st.warning("No country data available.")
    st.stop()

# Strip world/US aggregate rows
exclude = {"0000US", "00World", "World"}
top_df = top_df[~top_df["country_code"].isin(exclude)].head(top_n)

# ── Top-N horizontal bar ──────────────────────────────────────────────────────
style.sec(f"Top {top_n} — {attr_choice}")
vals  = [convert(v, commodity, imperial) for v in top_df["value"]]
names = top_df["country_name"].tolist()

fig_top = go.Figure(go.Bar(
    x=vals[::-1], y=names[::-1],
    orientation="h",
    marker_color=C_GREEN,
    hovertemplate="%{y}: %{x:,.1f}<extra></extra>",
))
fig_top.update_layout(**style.plot_layout(
    title=dict(text=f"{commodity} — Top {top_n} {attr_choice} ({mkt_year}/{str(mkt_year+1)[-2:]})",
               font=dict(size=13)),
    xaxis_title=ul,
    height=max(300, top_n * 28),
    margin=dict(l=130, r=8, t=36, b=8),
))
st.plotly_chart(fig_top, use_container_width=True)

# ── Side-by-side: producers vs exporters ──────────────────────────────────────
style.sec("Production vs Exports")
c1, c2 = st.columns(2)

@st.cache_data(ttl=3600, show_spinner=False)
def _top_sf(commodity, mkt_year, attr_name, n):
    if db.snowflake_available():
        return db.top_countries(commodity, mkt_year, attr_name, n)
    return None

with c1:
    prod_df = _top_sf(commodity, mkt_year, "Production", 8)
    if prod_df is not None and not prod_df.empty:
        prod_df = prod_df[~prod_df["country_code"].isin(exclude)].head(8)
        fig_p = go.Figure(go.Bar(
            x=[convert(v, commodity, imperial) for v in prod_df["value"]][::-1],
            y=prod_df["country_name"].tolist()[::-1],
            orientation="h", marker_color=C_LIME,
        ))
        fig_p.update_layout(**style.plot_layout(
            title=dict(text="Top 8 Producers", font=dict(size=13)),
            xaxis_title=ul, height=280,
            margin=dict(l=110, r=8, t=36, b=8),
        ))
        st.plotly_chart(fig_p, use_container_width=True)

with c2:
    exp_df = _top_sf(commodity, mkt_year, "Exports", 8)
    if exp_df is not None and not exp_df.empty:
        exp_df = exp_df[~exp_df["country_code"].isin(exclude)].head(8)
        fig_e = go.Figure(go.Bar(
            x=[convert(v, commodity, imperial) for v in exp_df["value"]][::-1],
            y=exp_df["country_name"].tolist()[::-1],
            orientation="h", marker_color=C_BLUE,
        ))
        fig_e.update_layout(**style.plot_layout(
            title=dict(text="Top 8 Exporters", font=dict(size=13)),
            xaxis_title=ul, height=280,
            margin=dict(l=110, r=8, t=36, b=8),
        ))
        st.plotly_chart(fig_e, use_container_width=True)
