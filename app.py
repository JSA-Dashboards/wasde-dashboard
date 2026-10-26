"""
JSA WASDE Dashboard — Balance Sheet page (full USDA PSD history).

Multi-page app: this file = Page 1. Additional pages live in pages/.
History comes from JSA.WASDE.PSD_HISTORY, loaded by etl/fetch_psd_history.py.
"""

import pathlib
import sys
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def _fresh_modules():
    # Streamlit Cloud re-reads scripts on a push but keeps already-imported modules
    # (db, style, ...), so pushed fixes to them don't run until a Reboot.
    here = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "style.py").exists())
    seen = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in here.glob("*.py")))
    if getattr(sys, "_wasde_code", None) == seen:
        return
    for name, mod in list(sys.modules.items()):
        path = getattr(mod, "__file__", None)
        if name != "__main__" and path and pathlib.Path(path).resolve().parent == here:
            sys.modules.pop(name, None)
    st.cache_data.clear()
    sys._wasde_code = seen


_fresh_modules()

import balance_sheet as bs  # noqa: E402
import bs_chart  # noqa: E402
import db  # noqa: E402
import massive  # noqa: E402
import psd_meta as pm  # noqa: E402
import style  # noqa: E402
from style import C_BLUE, C_GOLD, C_GREEN, C_LIME, C_RED  # noqa: E402

st.set_page_config(
    page_title="JSA WASDE — Balance Sheet",
    page_icon="🌾",
    layout="wide",
    initial_sidebar_state="auto",
)
style.inject()
style.nav_bar()

YEARS = list(range(1960, date.today().year + 2))
SNAPSHOT_COMMODITIES = {"Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
TABLE_COLORS = {"border": style.BORDER, "text": style.TEXT, "muted": style.TEXT_MUTED, "green": C_GREEN,
                "dark": "#3f4f44", "good": "#1f7a3d", "bad": C_RED}


def mkt(y: int) -> str:
    return f"{y}/{str(y + 1)[-2:]}"


if not db.snowflake_available():
    st.info("**Snowflake not configured.** The balance sheet reads JSA.WASDE.PSD_HISTORY.")
    style.disclaimer()
    st.stop()

# ── Controls ──────────────────────────────────────────────────────────────────
_loaded = set(db.psd_loaded_commodities())
COMMODITIES = [c for c, code_ in pm.COMMODITIES.items() if code_ in _loaded]
if not COMMODITIES:
    st.warning("USDA PSD history is still loading for the first time — try again shortly.")
    style.disclaimer()
    st.stop()

st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
c1, c2, c3, c4, c5 = st.columns([1.5, 1.5, 1, 1, 1.2])
with c1:
    commodity = st.selectbox("Commodity", COMMODITIES, key="bs_commodity")
code = pm.COMMODITIES[commodity]

ctry = db.psd_countries(code)
if ctry.empty:
    st.warning(f"USDA PSD history for **{commodity}** hasn't been loaded yet. It is loading now — try again shortly.")
    style.disclaimer()
    st.stop()
names = dict(zip(ctry.country_name, ctry.country_code))
ordered = [n for n in ("World", "United States") if n in names] + sorted(
    n for n in names if n not in ("World", "United States"))
with c2:
    region = st.selectbox("Region / country", ordered, key=f"bs_region_{code}")

df = db.psd_history(code, names[region])
if df.empty:
    st.warning(f"No PSD history for {commodity} in {region}.")
    style.disclaimer()
    st.stop()

basic = bs.basics(df)
N = bs.derive(basic)
first_year = int(N["production"].dropna().index.min()) if "production" in N else int(N.index.min())
latest = int(N["production"].dropna().index.max()) if "production" in N else int(N.index.max())

with c3:
    begin = st.selectbox("Begin year", YEARS, index=YEARS.index(max(2000, first_year)), key="bs_begin",
                         format_func=mkt)
with c4:
    end = st.selectbox("End year", YEARS, index=YEARS.index(latest), key="bs_end", format_func=mkt)
with c5:
    system = (st.segmented_control("Units", ["Metric", "Imperial"], default="Imperial", key="bs_units")
              or "Imperial").lower()
st.markdown("</div>", unsafe_allow_html=True)

b0 = max(begin, first_year)
e0 = min(end, latest)
if b0 > e0:
    st.warning("The begin year is after the end year — adjust the range.")
    style.disclaimer()
    st.stop()

labels = pm.unit_labels(commodity, system)
as_of = int(df["as_of"].max())
as_of_txt = f"{MONTHS[as_of % 100 - 1]} {as_of // 100}" if as_of else ""

# ── Title ─────────────────────────────────────────────────────────────────────
st.markdown(
    f"<div style='font-family:\"EB Garamond\",Georgia,serif;font-size:1.6rem;font-weight:600;"
    f"color:{style.TEXT};margin:4px 0 2px'>{region} {commodity} — Supply &amp; Demand Balance Sheet</div>"
    f"<div style='color:{style.TEXT_MUTED};font-size:0.85rem;margin-bottom:6px'>"
    f"{mkt(b0)} – {mkt(e0)} &nbsp;·&nbsp; {system.title()} &nbsp;·&nbsp; USDA FAS PSD"
    f"{' &nbsp;·&nbsp; updated ' + as_of_txt if as_of_txt else ''}</div>",
    unsafe_allow_html=True,
)

# ── KPI row (end year) ────────────────────────────────────────────────────────
cur_view = bs.to_display(bs.make_view(N, "current"), commodity, system,
                         [k for k, *_ in bs.ROWS if k in N.columns])


def kpi(col, label, key, pct=False):
    with col:
        v = cur_view.at[e0, key] if key in cur_view and e0 in cur_view.index else None
        pv = cur_view.at[e0 - 1, key] if key in cur_view and (e0 - 1) in cur_view.index else None
        if v is None or pd.isna(v):
            st.metric(label, "N/A")
            return
        dec = 1
        delta = None
        if pv is not None and not pd.isna(pv):
            delta = f"{v - pv:+,.1f}{'pp' if pct else ''} vs LY"
        st.metric(label, f"{v:,.{dec}f}{'%' if pct else ''}", delta)


cols = st.columns(6)
kpi(cols[0], f"Production {mkt(e0)}", "production")
kpi(cols[1], "Exports", "exports")
kpi(cols[2], "Total Use", "total_use")
kpi(cols[3], "Ending Stocks", "ending_stocks")
kpi(cols[4], "Stocks-to-Use", "stu", pct=True)
futures_slot = cols[5]          # filled after the table so the slow futures lookup never blocks it

# ── Balance sheet ─────────────────────────────────────────────────────────────
t1, t2 = st.columns([3, 1.4])
with t1:
    view_label = st.segmented_control("Show", list(bs.VIEWS), default="Current", key="bs_view") or "Current"
view = bs.VIEWS[view_label]

prev = None
if view == "lr":
    snap = {"World": "0000World", "United States": "0000US"}.get(region)
    if snap and commodity in SNAPSHOT_COMMODITIES:
        units = df.groupby("attribute_id")["unit_id"].agg(lambda s: s.mode().iat[0])
        prev = bs.previous_vintage(basic, db.latest_revisions(commodity, snap), units)

years = [y for y in range(b0, e0 + 1) if y in N.index]
keys = bs.visible_keys(N, years, commodity)
V = bs.make_view(N, view, prev)
D = bs.to_display(V, commodity, system, keys)

st.markdown(bs.render_html(D, years, keys, commodity, system, view, latest, TABLE_COLORS),
            unsafe_allow_html=True)

notes = {
    "current": "Current USDA estimates and forecasts; the shaded column is the current marketing year.",
    "lr": "Δ LR = change at the latest WASDE versus the previous one. Available for World and United States "
          "in corn, soybeans, wheat, cotton, rice and soybean oil, for the last three marketing years.",
    "ly": "Δ LY = change versus the prior marketing year (Stocks-to-Use in percentage points).",
    "5ya": "Δ 5YA = change versus the average of the previous five marketing years.",
}
st.caption(
    f"Units: {labels['qty']} · area {labels['area']} · yield {labels['yield']} · Stocks-to-Use = Ending Stocks ÷ Total Use "
    f"· Total Use = Domestic Consumption + Exports. {notes[view]}"
)
if view == "lr" and prev is None:
    st.info("Change vs last report isn't available for this commodity or region.")
with t2:
    st.download_button("Download table (CSV)", bs.to_csv(D, years, keys, commodity, system),
                       file_name=f"{commodity}_{region}_balance_sheet_{view}.csv".replace(" ", "_"),
                       mime="text/csv", width="stretch")

price, ticker = massive.front_month_price(commodity)
if price is not None:
    fut_val, fut_label = massive.fmt_futures(commodity, price, ticker)
    with futures_slot:
        st.metric(fut_label, fut_val)

# ── Chart any line item of the table above ────────────────────────────────────
style.sec("Chart any line")
bs_chart.show(cur_view, keys, years, labels, commodity, region, mkt)

# ── Trend charts for the selected range ───────────────────────────────────────
style.sec("Trends")
chart_years = [y for y in years]
ch1, ch2 = st.columns(2)

with ch1:
    fig = go.Figure()
    for key, name, color in (("production", "Production", C_LIME), ("total_use", "Total Use", C_BLUE)):
        if key in cur_view:
            fig.add_trace(go.Bar(x=[mkt(y) for y in chart_years], y=[cur_view.at[y, key] for y in chart_years],
                                 name=name, marker_color=color, opacity=0.9))
    if "ending_stocks" in cur_view:
        fig.add_trace(go.Scatter(x=[mkt(y) for y in chart_years],
                                 y=[cur_view.at[y, "ending_stocks"] for y in chart_years], name="Ending Stocks",
                                 mode="lines+markers", line=dict(color=C_GOLD, width=2.5), marker=dict(size=5),
                                 yaxis="y2"))
    fig.update_layout(**style.plot_layout(
        title=dict(text="Production · Total Use · Ending Stocks", font=dict(size=13)),
        barmode="group", legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0), xaxis=dict(**style._AXIS, nticks=14, tickangle=-45), margin=dict(l=10, r=10, t=85, b=45), yaxis=dict(title=labels["qty"], gridcolor=style.BORDER),
        yaxis2=dict(title="Ending Stocks", overlaying="y", side="right", gridcolor="rgba(0,0,0,0)", zeroline=False),
        height=340))
    st.plotly_chart(fig, width="stretch")

with ch2:
    if "stu" in cur_view:
        xs = [mkt(y) for y in chart_years]
        ys = [cur_view.at[y, "stu"] for y in chart_years]
        avg = pd.Series(ys, dtype=float).mean()
        fig2 = go.Figure(go.Bar(x=xs, y=ys, marker_color=[C_GOLD if y == latest else C_LIME for y in chart_years],
                                name="Stocks-to-Use"))
        fig2.add_hline(y=avg, line_dash="dot", line_color=style.TEXT_MUTED,
                       annotation_text=f"range avg {avg:.1f}%", annotation_position="top right")
        fig2.update_layout(**style.plot_layout(
            title=dict(text="Stocks-to-Use %", font=dict(size=13)),
            xaxis=dict(**style._AXIS, nticks=14, tickangle=-45), margin=dict(l=10, r=10, t=85, b=45), yaxis=dict(title="%", gridcolor=style.BORDER), height=340, showlegend=False))
        st.plotly_chart(fig2, width="stretch")

use_keys = [k for k in ("feed", "feed_waste", "crush", "food_use", "fsi", "industrial", "mill_use", "loss", "exports")
            if k in cur_view and cur_view.loc[chart_years, k].notna().any()]
if len(use_keys) >= 2:
    palette = [C_GOLD, "#d9b87a", C_GREEN, C_LIME, "#9b89c4", "#c4785a", "#8c8c8c", C_BLUE]
    fig3 = go.Figure()
    for i, k in enumerate(use_keys):
        fig3.add_trace(go.Bar(x=[mkt(y) for y in chart_years], y=[cur_view.at[y, k] for y in chart_years],
                              name=bs.LABEL[k], marker_color=palette[i % len(palette)]))
    fig3.update_layout(**style.plot_layout(
        title=dict(text="Use components (stacked)", font=dict(size=13)), barmode="stack", legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0), xaxis=dict(**style._AXIS, nticks=20, tickangle=-45), margin=dict(l=10, r=10, t=85, b=45), 
        yaxis=dict(title=labels["qty"], gridcolor=style.BORDER), height=340))
    st.plotly_chart(fig3, width="stretch")

style.disclaimer()
