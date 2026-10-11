"""
Page 5 — WASDE Tendencies
What does USDA typically do in each monthly WASDE report?
Average revision magnitude by release month across all tracked crop years.
"""

import plotly.graph_objects as go
import streamlit as st
import pandas as pd

import db
import style
from style import C_GREEN, C_RED, C_GOLD, SERIES_COLORS
from units import unit_label

st.set_page_config(
    page_title="JSA WASDE — Tendencies",
    page_icon="🔍",
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
    "Total Use", "Feed/Residual Use",
]

commodity, region, imperial = style.sidebar_nav(COMMODITIES)
country = "0000US" if region == "US" else "0000World"
ul = unit_label(commodity, imperial)

st.markdown(
    f"<h1 style='margin:0;font-size:1.75rem;font-weight:800'>"
    f"{commodity} — WASDE Tendencies</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='color:{style.TEXT_MUTED};font-size:0.83rem;margin-bottom:4px'>"
    f"Average USDA revision by release month across all tracked crop years &nbsp;·&nbsp; {region}</div>",
    unsafe_allow_html=True,
)
st.markdown("<hr style='margin:8px 0 16px'>", unsafe_allow_html=True)

if not db.snowflake_available():
    st.info(
        "**Snowflake not configured.** This page needs `JSA.WASDE.PSD_SNAPSHOTS`. "
        "Add credentials and run `python etl/fetch_wasde.py --backfill`."
    )
    st.stop()

with st.sidebar:
    sel_attr = st.selectbox("Line item", KEY_ATTRS)

# ── Load tendency data ────────────────────────────────────────────────────────
with st.spinner("Loading tendency data…"):
    tend_df = db.tendencies(commodity, sel_attr, country)

if tend_df.empty:
    st.warning(
        f"No revision history yet for **{commodity} — {sel_attr}**. "
        "The tendency analysis builds as monthly ETL runs accumulate."
    )
    st.stop()

tend_df["month_label"] = tend_df["release_month"].map(MONTH_LABELS)

# ── Average revision bar ──────────────────────────────────────────────────────
style.sec(f"Average Monthly Revision — {sel_attr}")
colors_bar = [C_GREEN if v >= 0 else C_RED for v in tend_df["avg_revision"]]
fig_avg = go.Figure(go.Bar(
    x=tend_df["month_label"],
    y=tend_df["avg_revision"],
    marker_color=colors_bar,
    error_y=dict(type="data", array=tend_df["std_revision"].tolist(),
                 visible=True, color=style.TEXT_MUTED, thickness=1.5),
    hovertemplate=(
        "<b>%{x}</b><br>"
        "Avg revision: %{y:+,.1f}<br>"
        "N observations: %{customdata}<extra></extra>"
    ),
    customdata=tend_df["n_obs"].tolist(),
))
fig_avg.add_hline(y=0, line_color=style.BORDER, line_width=1)
fig_avg.update_layout(**style.plot_layout(
    title=dict(
        text=f"{commodity} — Avg {sel_attr} Revision by WASDE Month ({region})",
        font=dict(size=13),
    ),
    yaxis_title=f"Avg revision ({ul})",
    height=340,
))
st.plotly_chart(fig_avg, use_container_width=True)

# ── Summary table ─────────────────────────────────────────────────────────────
style.sec("Revision Statistics by Month")
disp = tend_df[["month_label", "n_obs", "avg_revision",
                "std_revision", "min_revision", "max_revision"]].copy()
disp.columns = ["Month", "Observations", "Avg Revision", "Std Dev", "Min", "Max"]
disp = disp.set_index("Month")

def _color_avg(val):
    if val > 0: return f"color:{C_GREEN};font-weight:600"
    if val < 0: return f"color:{C_RED};font-weight:600"
    return ""

st.dataframe(
    disp.style
        .applymap(_color_avg, subset=["Avg Revision"])
        .format({
            "Avg Revision": "{:+,.1f}",
            "Std Dev": "{:,.1f}",
            "Min": "{:+,.1f}",
            "Max": "{:+,.1f}",
            "Observations": "{:.0f}",
        }, na_rep="—"),
    use_container_width=True,
    height=460,
)
st.caption(
    f"Units: {ul} &nbsp;·&nbsp; "
    "Revision = this month's estimate minus prior month's estimate. "
    "Builds as monthly ETL snapshots accumulate."
)

# ── Multi-attribute heatmap ───────────────────────────────────────────────────
with st.expander("Multi-attribute tendency heatmap"):
    heat_data = {}
    for attr in KEY_ATTRS:
        df_a = db.tendencies(commodity, attr, country)
        if not df_a.empty:
            df_a["month_label"] = df_a["release_month"].map(MONTH_LABELS)
            heat_data[attr] = df_a.set_index("month_label")["avg_revision"]

    if heat_data:
        heat_wide = pd.DataFrame(heat_data).reindex(list(MONTH_LABELS.values()))
        fig_heat = go.Figure(go.Heatmap(
            z=heat_wide.values.T.tolist(),
            x=heat_wide.index.tolist(),
            y=heat_wide.columns.tolist(),
            colorscale=[[0, C_RED], [0.5, "white"], [1, C_GREEN]],
            zmid=0,
            colorbar=dict(title=ul),
            hovertemplate="<b>%{y}</b><br>%{x}: %{z:+,.1f}<extra></extra>",
        ))
        fig_heat.update_layout(**style.plot_layout(
            title=dict(text=f"{commodity} — Average Revision Heatmap ({region})",
                       font=dict(size=13)),
            height=360,
            margin=dict(l=160, r=8, t=36, b=8),
        ))
        st.plotly_chart(fig_heat, use_container_width=True)
