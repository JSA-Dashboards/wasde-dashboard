"""
Page 3 — Bucket Trends
How each use component has shifted as a share of total use over time.
"""

import plotly.graph_objects as go
import streamlit as st
import pandas as pd

import db
import style
from style import C_BLUE, C_GOLD, C_GREEN, C_LIME, C_RED, C_ORANGE, SERIES_COLORS
from units import convert, fmt, unit_label

st.set_page_config(
    page_title="JSA WASDE — Bucket Trends",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)
style.inject()

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]

USE_BUCKETS = [
    ("Feed/Residual Use",       C_GOLD),
    ("Food/Seed/Industrial Use", C_LIME),
    ("Exports",                 C_BLUE),
]

st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
_c1, _c2, _c3 = st.columns([2, 1, 1])
with _c1:
    commodity = st.selectbox("Commodity", COMMODITIES)
with _c2:
    region = st.radio("Region", ["US", "World"], horizontal=True)
with _c3:
    imperial = st.toggle("Million bu", value=False)
st.markdown("</div>", unsafe_allow_html=True)

country = "0000US" if region == "US" else "0000World"
ul = unit_label(commodity, imperial)

st.markdown(
    f"<div style='font-family:\"EB Garamond\",Georgia,serif;font-size:1.35rem;"
    f"font-weight:600;color:{style.TEXT};margin-bottom:2px'>"
    f"{commodity} — Use Bucket Trends &nbsp;"
    f"<span style='font-size:0.95rem;color:{style.TEXT_MUTED};font-weight:400'>{region}</span></div>",
    unsafe_allow_html=True,
)
st.markdown(f"<hr style='margin:6px 0 14px;border-color:{style.BORDER}'>", unsafe_allow_html=True)

if not db.snowflake_available():
    st.info(
        "**Snowflake not configured.** Add credentials to `.env` / `st.secrets` "
        "and run `python etl/fetch_wasde.py --backfill`."
    )
    st.stop()

with st.spinner("Loading…"):
    bkt = db.bucket_history(commodity, country)

if bkt.empty:
    st.warning("No data found. Run the ETL backfill first.")
    st.stop()

# Pivot to wide
wide = bkt.pivot_table(index="market_year", columns="attribute_name",
                        values="value", aggfunc="first")

avail_years = sorted(wide.index.tolist(), reverse=True)
sel_year = avail_years[0] if avail_years else None

# ── KPI: selected year's bucket breakdown ─────────────────────────────────────
style.sec(f"Use Breakdown — {sel_year}/{str(sel_year+1)[-2:]}")
tu = convert(wide.loc[sel_year, "Total Use"] if sel_year in wide.index and "Total Use" in wide.columns else None,
             commodity, imperial)
k_cols = st.columns(len(USE_BUCKETS) + 2)
with k_cols[0]:
    st.metric("Total Use", f"{tu:,.1f} {ul}" if tu else "N/A")

for i, (attr, _) in enumerate(USE_BUCKETS):
    with k_cols[i+1]:
        v = convert(wide.loc[sel_year, attr] if sel_year in wide.index and attr in wide.columns else None,
                    commodity, imperial)
        pct = f"{v/tu*100:.1f}%" if (v and tu and tu > 0) else "—"
        st.metric(attr.split("/")[0], f"{pct}", f"{v:,.1f} {ul}" if v else None)

# 5-year avg comparison
yrs5 = [y for y in avail_years[:5] if y != sel_year]
with k_cols[-1]:
    if yrs5 and tu:
        es_col = "Ending Stocks"
        stu_vals = []
        for y in yrs5:
            es = convert(wide.loc[y, es_col] if y in wide.index and es_col in wide.columns else None,
                         commodity, imperial)
            tu2 = convert(wide.loc[y, "Total Use"] if y in wide.index and "Total Use" in wide.columns else None,
                          commodity, imperial)
            if es and tu2 and tu2 > 0:
                stu_vals.append(es/tu2*100)
        avg_stu = sum(stu_vals)/len(stu_vals) if stu_vals else None
        es_cur  = convert(wide.loc[sel_year, "Ending Stocks"] if sel_year in wide.index
                          and "Ending Stocks" in wide.columns else None, commodity, imperial)
        cur_stu = f"{es_cur/tu*100:.1f}%" if (es_cur and tu and tu > 0) else "N/A"
        avg_s   = f"5yr avg: {avg_stu:.1f}%" if avg_stu else None
        st.metric("S/U %", cur_stu, avg_s)

st.markdown("<br>", unsafe_allow_html=True)

# ── Stacked area: absolute values ─────────────────────────────────────────────
style.sec("Absolute Use by Component")
fig_abs = go.Figure()
for attr, color in USE_BUCKETS:
    if attr not in wide.columns:
        continue
    ys = [convert(wide.loc[y, attr], commodity, imperial) if y in wide.index else None
          for y in avail_years[::-1]]
    fig_abs.add_trace(go.Bar(
        x=avail_years[::-1], y=ys, name=attr.split("/")[0],
        marker_color=color,
    ))
fig_abs.update_layout(**style.plot_layout(
    title=dict(text=f"{commodity} Use Components — {region}", font=dict(size=13)),
    barmode="stack",
    yaxis_title=ul,
    height=320,
))
st.plotly_chart(fig_abs, use_container_width=True)

# ── Share chart ───────────────────────────────────────────────────────────────
style.sec("Share of Total Use (%)")
fig_share = go.Figure()
for attr, color in USE_BUCKETS:
    if attr not in wide.columns or "Total Use" not in wide.columns:
        continue
    shares = []
    for y in avail_years[::-1]:
        if y not in wide.index:
            shares.append(None)
            continue
        v  = wide.loc[y, attr]
        tu_v = wide.loc[y, "Total Use"]
        shares.append(v/tu_v*100 if (v and tu_v and tu_v > 0) else None)
    fig_share.add_trace(go.Scatter(
        x=avail_years[::-1], y=shares,
        name=attr.split("/")[0], stackgroup="one",
        line=dict(color=color), fillcolor=color,
        mode="lines",
    ))
fig_share.update_layout(**style.plot_layout(
    title=dict(text="% Share of Total Use — stacked", font=dict(size=13)),
    yaxis_title="%",
    height=300,
))
st.plotly_chart(fig_share, use_container_width=True)

# ── Selected year vs 5-yr avg bar ─────────────────────────────────────────────
style.sec(f"{sel_year}/{str(sel_year+1)[-2:]} vs 5-Year Average")
c1, c2 = st.columns(2)
attrs_to_compare = [a for a, _ in USE_BUCKETS if a in wide.columns]
cur_vals, avg_vals = [], []
for attr in attrs_to_compare:
    cv = convert(wide.loc[sel_year, attr] if sel_year in wide.index else None, commodity, imperial)
    avgs = [convert(wide.loc[y, attr] if y in wide.index else None, commodity, imperial) for y in yrs5]
    avgs = [v for v in avgs if v is not None]
    cur_vals.append(cv)
    avg_vals.append(sum(avgs)/len(avgs) if avgs else None)

labels = [a.split("/")[0] for a in attrs_to_compare]
with c1:
    fig_cmp = go.Figure()
    fig_cmp.add_trace(go.Bar(name=f"{sel_year}", x=labels, y=cur_vals,
                              marker_color=C_GREEN))
    fig_cmp.add_trace(go.Bar(name="5yr avg", x=labels, y=avg_vals,
                              marker_color=style.BORDER))
    fig_cmp.update_layout(**style.plot_layout(
        title=dict(text="Volume Comparison", font=dict(size=13)),
        barmode="group", yaxis_title=ul, height=280,
    ))
    st.plotly_chart(fig_cmp, use_container_width=True)

with c2:
    # Ending stocks trend
    if "Ending Stocks" in wide.columns and "Total Use" in wide.columns:
        stu_hist = {
            y: wide.loc[y, "Ending Stocks"] / wide.loc[y, "Total Use"] * 100
            for y in avail_years[::-1]
            if y in wide.index
            and wide.loc[y, "Ending Stocks"] is not None
            and wide.loc[y, "Total Use"] is not None
            and wide.loc[y, "Total Use"] > 0
        }
        colors_stu = [C_RED if v < 10 else C_GOLD if v < 15 else C_LIME
                      for v in stu_hist.values()]
        fig_stu = go.Figure(go.Bar(
            x=list(stu_hist.keys()), y=list(stu_hist.values()),
            marker_color=colors_stu, name="S/U %",
        ))
        fig_stu.add_hline(y=15, line_dash="dot", line_color=C_GOLD,
                          annotation_text="15% threshold", annotation_position="top right")
        fig_stu.update_layout(**style.plot_layout(
            title=dict(text="Stocks-to-Use % History", font=dict(size=13)),
            yaxis_title="%", height=280,
        ))
        st.plotly_chart(fig_stu, use_container_width=True)
