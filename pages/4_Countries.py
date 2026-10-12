"""
Page 4 — Country View
Top producers, exporters, importers by commodity.
Always fetches live from the USDA FAS PSD bulk API (Snowflake only stores US + World).
"""

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

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

# USDA FAS PSD commodity codes (same across old + new API)
COMM_CODES = {
    "Corn":        "0440000",
    "Soybeans":    "2222000",
    "Wheat":       "0410000",
    "Cotton":      "2631000",
    "Rice, Milled":"0422110",
    "Soybean Oil": "4232000",
}

# Attribute IDs from the USDA FAS PSD bulk data API
ATTR_IDS = {
    "Production":    28,
    "Exports":       88,
    "Imports":       40,
    "Ending Stocks": 176,
    "Total Use":     185,
}

# Country codes to exclude (world + US aggregates in various formats)
WORLD_CODES = {"0000US", "00World", "World", "00", "0000WO", "WorldTotal"}

PSD_BULK = "https://apps.fas.usda.gov/psdonline/api/data/get"

# ── Controls ───────────────────────────────────────────────────────────────────
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
    attr_choice = st.selectbox("Attribute", list(ATTR_IDS.keys()))
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


# ── Data fetch ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_psd(comm_code: str) -> pd.DataFrame:
    """Fetch all years/countries/attributes for one commodity from the PSD bulk API."""
    try:
        resp = requests.get(
            PSD_BULK,
            params={"commodityCode": comm_code},
            timeout=90,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        # Normalize column names to lowercase
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        return pd.DataFrame()


def _top(comm_code: str, attr_id: int, year: int, n: int) -> pd.DataFrame:
    df = _fetch_psd(comm_code)
    if df.empty:
        return pd.DataFrame()

    # Find the value column (may be 'value' or 'unitvalue')
    val_col = "value" if "value" in df.columns else None
    if val_col is None:
        return pd.DataFrame()

    # Normalize column names we care about
    attr_col = next((c for c in df.columns if "attributeid" in c.lower()), None)
    year_col = next((c for c in df.columns if "marketyear" in c.lower()), None)
    cc_col   = next((c for c in df.columns if "countrycode" == c.lower()), None)
    cn_col   = next((c for c in df.columns if "countryname" == c.lower()), None)

    if not all([attr_col, year_col, cc_col]):
        return pd.DataFrame()

    mask = (df[attr_col] == attr_id) & (df[year_col] == year)
    filtered = df[mask].copy()

    if filtered.empty:
        return pd.DataFrame()

    # Drop world/US-aggregate rows
    filtered = filtered[~filtered[cc_col].isin(WORLD_CODES)]
    filtered[val_col] = pd.to_numeric(filtered[val_col], errors="coerce").fillna(0)
    filtered = filtered[filtered[val_col] > 0]

    cols = {cc_col: "country_code", val_col: "value"}
    if cn_col:
        cols[cn_col] = "country_name"
    result = filtered.rename(columns=cols)

    if "country_name" not in result.columns:
        result["country_name"] = result["country_code"]

    return result.nlargest(n, "value")[["country_code", "country_name", "value"]].reset_index(drop=True)


with st.spinner("Loading country data from USDA FAS…"):
    top_df = _top(COMM_CODES[commodity], ATTR_IDS[attr_choice], mkt_year, top_n)

if top_df.empty:
    st.warning(
        f"No country-level data returned for **{commodity} — {attr_choice}** "
        f"in {mkt_year}/{str(mkt_year+1)[-2:]}. "
        "USDA FAS may not publish this attribute for the selected combination, "
        "or the data isn't available yet for this marketing year."
    )
    st.stop()

# ── Top-N horizontal bar ───────────────────────────────────────────────────────
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
    title=dict(
        text=f"{commodity} — Top {top_n} {attr_choice} ({mkt_year}/{str(mkt_year+1)[-2:]})",
        font=dict(size=13),
    ),
    xaxis_title=ul,
    height=max(300, top_n * 32),
    margin=dict(l=150, r=8, t=36, b=8),
))
st.plotly_chart(fig_top, use_container_width=True)

# ── Side-by-side: producers vs exporters ──────────────────────────────────────
style.sec("Production vs Exports")
c1, c2 = st.columns(2)


@st.cache_data(ttl=3600, show_spinner=False)
def _side(comm_code, attr_id, year, n):
    return _top(comm_code, attr_id, year, n)


with c1:
    prod_df = _side(COMM_CODES[commodity], ATTR_IDS["Production"], mkt_year, 8)
    if not prod_df.empty:
        fig_p = go.Figure(go.Bar(
            x=[convert(v, commodity, imperial) for v in prod_df["value"]][::-1],
            y=prod_df["country_name"].tolist()[::-1],
            orientation="h", marker_color=C_LIME,
        ))
        fig_p.update_layout(**style.plot_layout(
            title=dict(text="Top 8 Producers", font=dict(size=13)),
            xaxis_title=ul, height=300,
            margin=dict(l=130, r=8, t=36, b=8),
        ))
        st.plotly_chart(fig_p, use_container_width=True)

with c2:
    exp_df = _side(COMM_CODES[commodity], ATTR_IDS["Exports"], mkt_year, 8)
    if not exp_df.empty:
        fig_e = go.Figure(go.Bar(
            x=[convert(v, commodity, imperial) for v in exp_df["value"]][::-1],
            y=exp_df["country_name"].tolist()[::-1],
            orientation="h", marker_color=C_BLUE,
        ))
        fig_e.update_layout(**style.plot_layout(
            title=dict(text="Top 8 Exporters", font=dict(size=13)),
            xaxis_title=ul, height=300,
            margin=dict(l=130, r=8, t=36, b=8),
        ))
        st.plotly_chart(fig_e, use_container_width=True)

# ── Data table ─────────────────────────────────────────────────────────────────
with st.expander("Full data table"):
    disp = top_df.copy()
    disp["value"] = disp["value"].apply(lambda v: convert(v, commodity, imperial))
    disp = disp.rename(columns={
        "country_code": "Code",
        "country_name": "Country",
        "value": f"{attr_choice} ({ul})",
    })
    st.dataframe(
        disp.style.format({f"{attr_choice} ({ul})": "{:,.1f}"}),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(f"Source: USDA FAS PSD Online · Marketing year {mkt_year}/{str(mkt_year+1)[-2:]}")
