"""
JSA WASDE Dashboard — main entry point (Balance Sheet page).

Multi-page app: this file = Page 1. Additional pages live in pages/.
"""

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import db
import style
from style import C_BLUE, C_GOLD, C_GREEN, C_LIME, C_RED, SERIES_COLORS
from units import convert, fmt, unit_label

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="JSA WASDE — Balance Sheet",
    page_icon="🌾",
    layout="wide",
    initial_sidebar_state="expanded",
)
style.inject()

# ── USDA FAS PSD API (live balance sheet; no Snowflake needed) ────────────────
def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

PSD_VIZ_BASE = "https://apps.fas.usda.gov/PSDOnlineApi/api/CompositeVisualization"

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]

COMM_CODES = {
    "Corn":         "0440000",
    "Soybeans":     "2222000",
    "Wheat":        "0410000",
    "Cotton":       "2631000",
    "Rice, Milled": "0422110",
    "Soybean Oil":  "4232000",
}

US_COUNTRY    = "US"
WORLD_COUNTRY = "00"

# Attribute IDs (strings) → display names
ATTR = {
    "20":  "Beginning Stocks",
    "28":  "Production",
    "40":  "Imports",
    "125": "Total Supply",
    "57":  "Feed/Residual Use",
    "58":  "Food/Seed/Industrial Use",
    "88":  "Exports",
    "185": "Total Use",
    "176": "Ending Stocks",
}

BALANCE_ORDER = ["20", "28", "40", "125", "57", "58", "88", "185", "176"]
TOTAL_ROWS    = {"Total Supply", "Total Use"}
STOCK_ROWS    = {"Beginning Stocks", "Ending Stocks"}


REGION_CODE = "R00"

@st.cache_resource
def _http():
    s = requests.Session()
    s.headers.update({"User-Agent": "JPSI-WASDE/1.0"})
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


@st.cache_data(ttl=3600, show_spinner=False)
def _get_latest_period() -> str:
    try:
        r = _http().get(f"{PSD_VIZ_BASE}/GetReportingPeriods", timeout=20)
        r.raise_for_status()
        return r.json()[0]["value"]
    except Exception:
        from datetime import datetime
        now = datetime.now()
        return f"{now.year}{now.month:02d}"


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_balance_sheet(commodity_code: str, country_code: str,
                         reporting_period: str) -> dict:
    try:
        r = _http().post(f"{PSD_VIZ_BASE}/GetAllAttributeData",
                         json={
                             "reportingPeriod":       reporting_period,
                             "reportingCutoffPeriod": None,
                             "commodityCode":         commodity_code,
                             "regionCode":            REGION_CODE,
                             "countryCode":           country_code,
                             "attributeId":           "4",
                         }, timeout=60)
        r.raise_for_status()
        return r.json() or {}
    except Exception:
        return {}


def _parse_viz_response(data: dict) -> pd.DataFrame:
    """Convert GetAllAttributeData response to long-form DataFrame."""
    headers = data.get("headerValues", [])
    rows = []
    for attr in data.get("attributeValues", []):
        attr_id   = str(attr.get("attributeId", "")).strip()
        attr_name = attr.get("attributeName", "").strip()
        vals      = attr.get("attributeValues", [])
        # headers[1:] maps to vals; skip idx 1 (Change) and idx 2 (prior period dup)
        for col_i, hdr in enumerate(headers[1:]):
            if col_i in (1, 2):
                continue  # Change column, prior-period duplicate
            import re
            m = re.match(r"(\d{2})/\d{2}", hdr.strip())
            if not m or col_i >= len(vals):
                continue
            myr = 2000 + int(m.group(1))
            raw = vals[col_i]
            if raw is None or raw == "":
                continue
            raw_str = str(raw).split("(")[0].strip().replace(",", "")
            try:
                value = float(raw_str)
            except ValueError:
                continue
            rows.append({
                "attributeId":   attr_id,
                "attributeName": attr_name,
                "marketYear":    myr,
                "value":         value,
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _val(df: pd.DataFrame, attr_id: str, year: int) -> float | None:
    if df.empty:
        return None
    row = df[(df["attributeId"] == str(attr_id)) & (df["marketYear"] == year)]
    return float(row["value"].values[0]) if not row.empty else None


def _ts(df: pd.DataFrame, attr_id: str) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    sub = df[df["attributeId"] == str(attr_id)].sort_values("marketYear")
    return sub.set_index("marketYear")["value"].astype(float)


# ── Sidebar ───────────────────────────────────────────────────────────────────
commodity, region, imperial = style.sidebar_nav(COMMODITIES)
comm_code   = COMM_CODES[commodity]
country_code = US_COUNTRY if region == "US" else WORLD_COUNTRY

with st.sidebar:
    rp     = _get_latest_period()
    rp_yr  = int(rp[:4])
    all_years = list(range(rp_yr + 1, 2004, -1))
    mkt_year  = st.selectbox("Marketing Year", all_years, index=0,
                              format_func=lambda y: f"{y}/{str(y+1)[-2:]}")
    hist_from = st.selectbox("History from", list(range(2005, mkt_year + 1)),
                              index=0,
                              format_func=lambda y: f"{y}/{str(y+1)[-2:]}")

# ── Load data ─────────────────────────────────────────────────────────────────
with st.spinner("Loading PSD data…"):
    raw = _fetch_balance_sheet(comm_code, country_code, rp)
    df  = _parse_viz_response(raw)
    df_hist = df[df["marketYear"] >= hist_from] if not df.empty else df

ul = unit_label(commodity, imperial)

# ── Header ────────────────────────────────────────────────────────────────────
c1, c2 = st.columns([6, 2])
with c1:
    st.markdown(
        f"<h1 style='margin:0;font-size:1.75rem;font-weight:800;color:{style.TEXT}'>"
        f"{commodity} — {region} Supply & Demand</h1>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:{style.TEXT_MUTED};font-size:0.83rem;margin-bottom:4px'>"
        f"Marketing Year {mkt_year}/{str(mkt_year+1)[-2:]} &nbsp;·&nbsp; "
        f"USDA FAS PSD &nbsp;·&nbsp; Units: {ul}</div>",
        unsafe_allow_html=True,
    )
with c2:
    st.markdown("<div style='text-align:right'>", unsafe_allow_html=True)

st.markdown("<hr style='margin:8px 0 16px'>", unsafe_allow_html=True)

# ── KPI row ───────────────────────────────────────────────────────────────────
def _kpi(label, attr_id, invert_delta=False):
    cur  = _val(df, attr_id, mkt_year)
    prev = _val(df, attr_id, mkt_year - 1)
    v    = convert(cur,  commodity, imperial)
    vp   = convert(prev, commodity, imperial)
    disp = f"{v:,.1f}" if v is not None else "N/A"
    if v is not None and vp is not None:
        delta = v - vp
        delta_s = f"{delta:+,.1f} {ul}"
    else:
        delta_s = None
    st.metric(label, disp, delta_s)

k1, k2, k3, k4, k5 = st.columns(5)
with k1: _kpi("Production",    "28")
with k2: _kpi("Exports",       "88")
with k3: _kpi("Total Use",    "185")
with k4: _kpi("Ending Stocks","176")
with k5:
    es  = convert(_val(df, "176", mkt_year),  commodity, imperial)
    tu  = convert(_val(df, "185", mkt_year),  commodity, imperial)
    esp = convert(_val(df, "176", mkt_year-1),commodity, imperial)
    tup = convert(_val(df, "185", mkt_year-1),commodity, imperial)
    stu  = f"{es/tu*100:.1f}%"  if (es and tu and tu > 0) else "N/A"
    stup = f"{(es/tu - esp/tup)*100:+.1f}pp" if (es and tu and esp and tup and tu > 0 and tup > 0) else None
    st.metric("Stocks / Use", stu, stup)

st.markdown("<br>", unsafe_allow_html=True)

# ── Two-column layout: balance sheet + charts ─────────────────────────────────
col_bs, col_charts = st.columns([1, 1.7])

with col_bs:
    style.sec("Supply & Demand Balance Sheet")

    yrs_show = [y for y in [mkt_year-2, mkt_year-1, mkt_year]
                if not df.empty and y in df["marketYear"].values]
    bs_data = {"Line Item": [ATTR[a] for a in BALANCE_ORDER]}
    for yr in yrs_show:
        col_label = f"{yr}/{str(yr+1)[-2:]}"
        bs_data[col_label] = [
            fmt(_val(df, a, yr), commodity, imperial)
            for a in BALANCE_ORDER
        ]
    bs_df = pd.DataFrame(bs_data).set_index("Line Item")

    def _style_bs(s):
        styles = []
        for idx in s.index:
            if idx in TOTAL_ROWS:
                styles.append("font-weight:700;border-top:1px solid #dde5de")
            elif idx in STOCK_ROWS:
                styles.append(f"color:{C_GOLD};font-weight:700")
            else:
                styles.append("")
        return styles

    st.dataframe(
        bs_df.style.apply(_style_bs, axis=0)
             .set_properties(**{"font-size": "0.86rem"}),
        use_container_width=True,
        height=360,
    )
    st.caption(f"Units: {ul}  ·  Source: USDA FAS PSD")

with col_charts:
    prod_ts   = _ts(df_hist, "28")
    use_ts    = _ts(df_hist, "185")
    stocks_ts = _ts(df_hist, "176")

    fig = go.Figure()
    fig.add_trace(go.Bar(x=prod_ts.index, y=[convert(v, commodity, imperial) for v in prod_ts],
                         name="Production", marker_color=C_LIME, opacity=0.85))
    fig.add_trace(go.Bar(x=use_ts.index,  y=[convert(v, commodity, imperial) for v in use_ts],
                         name="Total Use",  marker_color=C_BLUE, opacity=0.70))
    fig.add_trace(go.Scatter(
        x=stocks_ts.index,
        y=[convert(v, commodity, imperial) for v in stocks_ts],
        name="Ending Stocks",
        line=dict(color=C_GOLD, width=2.5),
        mode="lines+markers", marker=dict(size=5),
        yaxis="y2",
    ))
    fig.update_layout(
        **style.plot_layout(
            title=dict(text="Production · Total Use · Ending Stocks", font=dict(size=13)),
            barmode="group",
            yaxis=dict(title=ul, gridcolor=style.BORDER),
            yaxis2=dict(title="Ending Stocks", overlaying="y", side="right",
                        gridcolor="rgba(0,0,0,0)", zeroline=False),
            height=310,
        )
    )
    st.plotly_chart(fig, use_container_width=True)

    # S/U ratio bar
    stu_ts = (stocks_ts / use_ts * 100).dropna()
    colors = [C_RED if v < 10 else C_GOLD if v < 15 else C_LIME for v in stu_ts]
    fig2 = go.Figure(go.Bar(x=stu_ts.index, y=stu_ts.values,
                             marker_color=colors, name="S/U %"))
    fig2.update_layout(**style.plot_layout(
        title=dict(text="Stocks-to-Use %", font=dict(size=13)),
        yaxis=dict(title="%", gridcolor=style.BORDER),
        height=210,
    ))
    st.plotly_chart(fig2, use_container_width=True)

# ── Use breakdown ─────────────────────────────────────────────────────────────
style.sec("Domestic Use Breakdown")
u1, u2 = st.columns(2)

with u1:
    feed_ts = _ts(df_hist, "57")
    fsi_ts  = _ts(df_hist, "58")
    exp_ts  = _ts(df_hist, "88")
    idx = sorted(set(feed_ts.index) | set(fsi_ts.index) | set(exp_ts.index))
    fig3 = go.Figure()
    for ts, name, color in [(feed_ts, "Feed/Residual", C_GOLD),
                             (fsi_ts,  "Food/Seed/Industrial", C_LIME),
                             (exp_ts,  "Exports", C_BLUE)]:
        vals = [convert(ts.get(y), commodity, imperial) for y in idx]
        fig3.add_trace(go.Bar(x=idx, y=vals, name=name, marker_color=color))
    fig3.update_layout(**style.plot_layout(
        title=dict(text="Use Components (stacked)", font=dict(size=13)),
        barmode="stack",
        yaxis=dict(title=ul, gridcolor=style.BORDER),
        height=280,
    ))
    st.plotly_chart(fig3, use_container_width=True)

with u2:
    pie_vals = {
        "Feed/Residual":         convert(_val(df, "57", mkt_year), commodity, imperial),
        "Food/Seed/Industrial":  convert(_val(df, "58", mkt_year), commodity, imperial),
        "Exports":               convert(_val(df, "88", mkt_year), commodity, imperial),
    }
    pie_vals = {k: v for k, v in pie_vals.items() if v and v > 0}
    if pie_vals:
        fig4 = go.Figure(go.Pie(
            labels=list(pie_vals.keys()),
            values=list(pie_vals.values()),
            hole=0.42,
            marker_colors=[C_GOLD, C_LIME, C_BLUE],
            textfont=dict(color=style.TEXT, size=12),
        ))
        fig4.update_layout(**style.plot_layout(
            title=dict(text=f"Use Mix — {mkt_year}/{str(mkt_year+1)[-2:]}",
                       font=dict(size=13)),
            height=280,
        ))
        st.plotly_chart(fig4, use_container_width=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    f"<div style='text-align:center;color:{style.TEXT_MUTED};font-size:0.72rem;"
    f"margin-top:24px;padding-top:12px;border-top:1px solid {style.BORDER}'>"
    "Data: USDA Foreign Agricultural Service — PSD Online &nbsp;·&nbsp; "
    "Updated monthly with WASDE release</div>",
    unsafe_allow_html=True,
)
