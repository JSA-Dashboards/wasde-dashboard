"""
Page 2 — WASDE Progression
How USDA revised its estimates month-by-month through the crop year.
Powered by JSA.WASDE.PSD_SNAPSHOTS (Snowflake).
Falls back to a friendly message when Snowflake isn't configured yet.
"""

import plotly.graph_objects as go
import streamlit as st

import db
import style
from style import C_BLUE, C_GOLD, C_GREEN, C_LIME, C_RED, SERIES_COLORS
from units import convert, fmt, unit_label

st.set_page_config(
    page_title="JSA WASDE — Progression",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
style.inject()

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]

MONTH_LABELS = {
    1:"Jan", 2:"Feb", 3:"Mar", 4:"Apr", 5:"May", 6:"Jun",
    7:"Jul", 8:"Aug", 9:"Sep", 10:"Oct", 11:"Nov", 12:"Dec",
}

KEY_ATTRS = [
    "Production", "Ending Stocks", "Exports",
    "Total Use", "Feed/Residual Use", "Food/Seed/Industrial Use",
]

commodity, region, imperial = style.sidebar_nav(COMMODITIES)
country = "0000US" if region == "US" else "0000World"
ul = unit_label(commodity, imperial)

with st.sidebar:
    mkt_year = st.selectbox(
        "Marketing Year", list(range(2026, 2004, -1)), index=0,
        format_func=lambda y: f"{y}/{str(y+1)[-2:]}")

st.markdown(
    f"<h1 style='margin:0;font-size:1.75rem;font-weight:800'>"
    f"{commodity} — WASDE Progression</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='color:{style.TEXT_MUTED};font-size:0.83rem;margin-bottom:4px'>"
    f"How estimates evolved across monthly WASDE releases — "
    f"{mkt_year}/{str(mkt_year+1)[-2:]} &nbsp;·&nbsp; {region}</div>",
    unsafe_allow_html=True,
)
st.markdown("<hr style='margin:8px 0 16px'>", unsafe_allow_html=True)

if not db.snowflake_available():
    st.info(
        "**Snowflake not configured.** This page reads from `JSA.WASDE.PSD_SNAPSHOTS`. "
        "Run the ETL backfill then add Snowflake credentials to `.env` / `st.secrets`.\n\n"
        "```\npython etl/fetch_wasde.py --backfill\n```"
    )
    st.stop()

# ── Load revision data ────────────────────────────────────────────────────────
with st.spinner("Loading revision series…"):
    rev_df = db.multi_attr_revisions(commodity, mkt_year, country)

if rev_df.empty:
    st.warning(
        f"No snapshots found for **{commodity} {mkt_year}/{str(mkt_year+1)[-2:]}** "
        f"in the database. Run `python etl/fetch_wasde.py --latest` to load data."
    )
    st.stop()

# Available attributes (intersect with KEY_ATTRS for default)
avail_attrs = sorted(rev_df["attribute_name"].unique())
default_sel  = [a for a in KEY_ATTRS if a in avail_attrs][:4]
sel_attrs    = st.multiselect("Line items to track", avail_attrs,
                               default=default_sel)

if not sel_attrs:
    st.info("Select at least one line item above.")
    st.stop()

filt = rev_df[rev_df["attribute_name"].isin(sel_attrs)].copy()

# Build pivot: rows = release month label, cols = attribute
def _month_label(row):
    return MONTH_LABELS.get(int(row["release_month"]),
                             str(int(row["release_month"])))

filt["month_label"] = filt.apply(_month_label, axis=1)

# ── Progression line chart ────────────────────────────────────────────────────
style.sec("Estimate Progression by WASDE Release Month")
fig = go.Figure()
for i, attr in enumerate(sel_attrs):
    sub = filt[filt["attribute_name"] == attr].sort_values("release_ym")
    if sub.empty:
        continue
    ys = [convert(v, commodity, imperial) for v in sub["value"]]
    fig.add_trace(go.Scatter(
        x=sub["month_label"].tolist(),
        y=ys,
        name=attr,
        line=dict(color=SERIES_COLORS[i % len(SERIES_COLORS)], width=2.5),
        mode="lines+markers",
        marker=dict(size=7),
        hovertemplate=f"<b>{attr}</b><br>%{{x}}: %{{y:,.1f}} {ul}<extra></extra>",
    ))
fig.update_layout(**style.plot_layout(
    title=dict(text=f"{commodity} {mkt_year}/{str(mkt_year+1)[-2:]} — WASDE Revisions",
               font=dict(size=13)),
    xaxis_title="WASDE Release Month",
    yaxis_title=ul,
    height=380,
))
st.plotly_chart(fig, use_container_width=True)

# ── Month-over-month revision table ──────────────────────────────────────────
style.sec("Month-over-Month Change (revision vs prior release)")
c1, c2 = st.columns(2)

with c1:
    pivot_val = (filt.pivot_table(index="month_label",
                                   columns="attribute_name",
                                   values="value",
                                   aggfunc="first")
                     .reindex(list(MONTH_LABELS.values()))
                     .dropna(how="all"))
    if not pivot_val.empty:
        st.dataframe(
            pivot_val.style.format("{:,.1f}", na_rep="—"),
            use_container_width=True,
            height=360,
        )

with c2:
    pivot_rev = (filt.pivot_table(index="month_label",
                                   columns="attribute_name",
                                   values="revision",
                                   aggfunc="first")
                     .reindex(list(MONTH_LABELS.values()))
                     .dropna(how="all"))

    def _color_rev(val):
        if val != val or val is None:  # NaN
            return f"color:{style.TEXT_MUTED}"
        return f"color:{C_GREEN}" if val > 0 else f"color:{C_RED}" if val < 0 else ""

    if not pivot_rev.empty:
        st.dataframe(
            pivot_rev.style.applymap(_color_rev).format("{:+,.1f}", na_rep="—"),
            use_container_width=True,
            height=360,
        )
        st.caption("Green = upward revision · Red = downward revision")

# ── Prior-year comparison ─────────────────────────────────────────────────────
with st.expander("Compare with prior marketing year"):
    prev_df = db.multi_attr_revisions(commodity, mkt_year - 1, country)
    if prev_df.empty:
        st.info(f"No data for {mkt_year-1}/{str(mkt_year)[-2:]}.")
    else:
        prev_filt = prev_df[prev_df["attribute_name"].isin(sel_attrs)].copy()
        prev_filt["month_label"] = prev_filt.apply(_month_label, axis=1)
        fig2 = go.Figure()
        for i, attr in enumerate(sel_attrs):
            sub_cur  = filt[filt["attribute_name"] == attr].sort_values("release_ym")
            sub_prev = prev_filt[prev_filt["attribute_name"] == attr].sort_values("release_ym")
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            if not sub_cur.empty:
                fig2.add_trace(go.Scatter(
                    x=sub_cur["month_label"], name=f"{attr} ({mkt_year})",
                    y=[convert(v, commodity, imperial) for v in sub_cur["value"]],
                    line=dict(color=color, width=2.5), mode="lines+markers",
                ))
            if not sub_prev.empty:
                fig2.add_trace(go.Scatter(
                    x=sub_prev["month_label"], name=f"{attr} ({mkt_year-1})",
                    y=[convert(v, commodity, imperial) for v in sub_prev["value"]],
                    line=dict(color=color, width=1.5, dash="dot"),
                    mode="lines+markers", marker=dict(size=5),
                ))
        fig2.update_layout(**style.plot_layout(
            title=dict(text=f"Current vs Prior Year — {commodity}", font=dict(size=13)),
            yaxis_title=ul, height=350,
        ))
        st.plotly_chart(fig2, use_container_width=True)
