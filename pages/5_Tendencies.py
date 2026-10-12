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
    initial_sidebar_state="auto",
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

st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
_c1, _c2, _c3, _c4, _c5 = st.columns([2, 1, 1, 1, 1])
with _c1:
    commodity = st.selectbox("Commodity", COMMODITIES)
with _c2:
    region = st.radio("Region", ["US", "World"], horizontal=True)
with _c3:
    imperial = st.toggle("Million bu", value=False)
with _c4:
    sel_attr = st.selectbox("Line item", KEY_ATTRS)
with _c5:
    mkt_year = st.selectbox("Mkt Year", list(range(2026, 2004, -1)), index=0,
                             format_func=lambda y: f"{y}/{str(y+1)[-2:]}")
st.markdown("</div>", unsafe_allow_html=True)

country = "0000US" if region == "US" else "0000World"
ul = unit_label(commodity, imperial)

st.markdown(
    f"<div style='font-family:\"EB Garamond\",Georgia,serif;font-size:1.35rem;"
    f"font-weight:600;color:{style.TEXT};margin-bottom:2px'>"
    f"{commodity} — WASDE Tendencies &nbsp;"
    f"<span style='font-size:0.95rem;color:{style.TEXT_MUTED};font-weight:400'>{region}</span></div>",
    unsafe_allow_html=True,
)
st.markdown(f"<hr style='margin:6px 0 14px;border-color:{style.BORDER}'>", unsafe_allow_html=True)

if not db.snowflake_available():
    st.info(
        "**Snowflake not configured.** This page needs `JSA.WASDE.PSD_SNAPSHOTS`. "
        "Add credentials and run `python etl/fetch_wasde.py --backfill`."
    )
    st.stop()

# ── Delta summary for selected mkt_year ───────────────────────────────────────
from units import convert

@st.cache_data(ttl=3600, show_spinner=False)
def _bs(commodity, year, country):
    return db.latest_balance_sheet(commodity, year, country)

@st.cache_data(ttl=3600, show_spinner=False)
def _rev(commodity, year, attr, country):
    return db.revision_series(commodity, year, attr, country)

style.sec(f"Current Estimates vs Prior WASDE, Prior Year & 5-Year Avg — {mkt_year}/{str(mkt_year+1)[-2:]}")

rev_ser = _rev(commodity, mkt_year, sel_attr, country)
if not rev_ser.empty:
    latest_row = rev_ser.sort_values("release_ym").iloc[-1]
    cur_raw    = float(latest_row["value"])
    rev_raw    = latest_row["revision"]
    cur_c      = convert(cur_raw, commodity, imperial)
    last_mo    = int(latest_row["release_month"])
    last_yr    = int(latest_row["release_year"])

    delta_lr = convert(rev_raw, commodity, imperial) \
               if (rev_raw is not None and str(rev_raw) != "nan") else None

    bs_ly  = _bs(commodity, mkt_year - 1, country)
    ly_map = bs_ly.set_index("attribute_name")["value"].to_dict() if not bs_ly.empty else {}
    ly_raw = ly_map.get(sel_attr)
    delta_ly = (cur_c - convert(ly_raw, commodity, imperial)) if ly_raw is not None else None

    five_vals = []
    for y in range(mkt_year - 5, mkt_year):
        bs_y = _bs(commodity, y, country)
        if not bs_y.empty:
            v = bs_y.set_index("attribute_name")["value"].to_dict().get(sel_attr)
            if v is not None:
                five_vals.append(convert(v, commodity, imperial))
    delta_5ya = (cur_c - sum(five_vals) / len(five_vals)) if five_vals else None

    def _fmt_d(v):
        return f"{v:+,.1f}" if v is not None else "—"

    def _color_delta(val):
        if not isinstance(val, str) or val == "—":
            return ""
        try:
            n = float(val.replace(",", ""))
            if n > 0: return f"color:{C_GREEN};font-weight:600"
            if n < 0: return f"color:{C_RED};font-weight:600"
        except ValueError:
            pass
        return ""

    summary = pd.DataFrame([{
        "Attribute":           sel_attr,
        f"Current ({ul})":     f"{cur_c:,.1f}",
        "Δ LR":                _fmt_d(delta_lr),
        "Δ LY":                _fmt_d(delta_ly),
        "Δ 5YA":               _fmt_d(delta_5ya),
    }]).set_index("Attribute")

    delta_cols = ["Δ LR", "Δ LY", "Δ 5YA"]
    st.dataframe(
        summary.style
               .map(_color_delta, subset=delta_cols)
               .set_properties(**{"font-size": "0.86rem"}),
        use_container_width=True,
        height=88,
    )
    last_mo_lbl = MONTH_LABELS.get(last_mo, str(last_mo))
    st.caption(
        f"Units: {ul}  ·  "
        f"Δ LR = revision in {last_mo_lbl} {last_yr} WASDE  ·  "
        f"Δ LY = vs {mkt_year-1}/{str(mkt_year)[-2:]}  ·  "
        f"Δ 5YA = vs {mkt_year-5}–{mkt_year-1} avg"
    )
else:
    st.info(f"No revision data yet for {commodity} — {sel_attr} in {mkt_year}/{str(mkt_year+1)[-2:]}.")

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
        .map(_color_avg, subset=["Avg Revision"])
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
