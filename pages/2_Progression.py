"""
Page 2 — WASDE Progression
How USDA revised its estimates month-by-month through the crop year.
Powered by JSA.WASDE.PSD_SNAPSHOTS (Snowflake).
Falls back to a friendly message when Snowflake isn't configured yet.
"""

import plotly.graph_objects as go
import streamlit as st

import pathlib
import sys


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

import db
import style
import us_progression
from style import C_BLUE, C_GOLD, C_GREEN, C_LIME, C_RED, SERIES_COLORS
from units import convert, fmt, unit_label

st.set_page_config(
    page_title="JSA WASDE — Progression",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
)
style.inject()
style.nav_bar()

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]

MONTH_LABELS = {
    1:"Jan", 2:"Feb", 3:"Mar", 4:"Apr", 5:"May", 6:"Jun",
    7:"Jul", 8:"Aug", 9:"Sep", 10:"Oct", 11:"Nov", 12:"Dec",
}

KEY_ATTRS = [
    "Production", "Ending Stocks", "Exports",
    "Total Use", "Feed/Residual Use", "Food/Seed/Industrial Use",
]

st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
_c1, _c2, _c3, _c4 = st.columns([2, 1, 1, 1])
with _c1:
    commodity = st.selectbox("Commodity", COMMODITIES)
with _c2:
    region = st.radio("Region", ["US", "World"], horizontal=True)
with _c3:
    imperial = st.toggle("Million bu", value=False)
with _c4:
    mkt_year = st.selectbox("Mkt Year", list(range(2026, 2004, -1)), index=0,
                             format_func=lambda y: f"{y}/{str(y+1)[-2:]}")
st.markdown("</div>", unsafe_allow_html=True)

country = "0000US" if region == "US" else "0000World"
ul = unit_label(commodity, imperial)

st.markdown(
    f"<div style='font-family:\"EB Garamond\",Georgia,serif;font-size:1.35rem;"
    f"font-weight:600;color:{style.TEXT};margin-bottom:2px'>"
    f"{commodity} — WASDE Progression &nbsp;"
    f"<span style='font-size:0.95rem;color:{style.TEXT_MUTED};font-weight:400'>"
    f"{mkt_year}/{str(mkt_year+1)[-2:]} &nbsp;·&nbsp; {region}</span></div>",
    unsafe_allow_html=True,
)
st.markdown(f"<hr style='margin:6px 0 14px;border-color:{style.BORDER}'>", unsafe_allow_html=True)

if not db.snowflake_available():
    st.info(
        "**Snowflake not configured.** This page reads from `JSA.WASDE.PSD_SNAPSHOTS`. "
        "Run the ETL backfill then add Snowflake credentials to `.env` / `st.secrets`.\n\n"
        "```\npython etl/fetch_wasde.py --backfill\n```"
    )
    st.stop()

# ── U.S. S&D by WASDE release (the Excel 'WASDE Progression' template) ───────
if region == "US" and commodity in ("Corn", "Wheat", "Soybeans"):
    us_progression.show(commodity, mkt_year, {
        "border": style.BORDER, "text": style.TEXT, "muted": style.TEXT_MUTED, "green": C_GREEN,
        "dark": "#3f4f44", "good": "#1f7a3d", "bad": C_RED})

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

# ── Delta summary table ───────────────────────────────────────────────────────
style.sec("Current Estimates vs Prior WASDE, Prior Year & 5-Year Avg")

# Latest estimate + Δ LR from revision series (already loaded)
latest_rows = (filt.sort_values("release_ym")
                   .groupby("attribute_name")
                   .last()
                   .reset_index())

# Latest balance sheets for prior years (Δ LY + Δ 5YA)
@st.cache_data(ttl=3600, show_spinner=False)
def _bs(commodity, year, country):
    return db.latest_balance_sheet(commodity, year, country)

bs_ly = _bs(commodity, mkt_year - 1, country)
ly_map = (bs_ly.set_index("attribute_name")["value"].to_dict()
          if not bs_ly.empty else {})

five_yr_maps = {}
for y in range(mkt_year - 5, mkt_year):
    bs_y = _bs(commodity, y, country)
    if not bs_y.empty:
        five_yr_maps[y] = bs_y.set_index("attribute_name")["value"].to_dict()

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

delta_rows = []
for attr in sel_attrs:
    row = latest_rows[latest_rows["attribute_name"] == attr]
    if row.empty:
        continue
    cur_raw = float(row["value"].values[0])
    rev_raw = row["revision"].values[0]
    cur_c   = convert(cur_raw, commodity, imperial)

    delta_lr  = convert(rev_raw, commodity, imperial) if rev_raw is not None and str(rev_raw) != "nan" else None
    ly_raw    = ly_map.get(attr)
    delta_ly  = (cur_c - convert(ly_raw, commodity, imperial)) if ly_raw is not None else None

    five_vals = [convert(m.get(attr), commodity, imperial)
                 for m in five_yr_maps.values() if m.get(attr) is not None]
    delta_5ya = (cur_c - sum(five_vals) / len(five_vals)) if five_vals else None

    # which WASDE month is this?
    last_mo = int(row["release_month"].values[0])
    last_yr = int(row["release_year"].values[0])
    delta_rows.append({
        "Attribute":     attr,
        f"Current ({ul})": f"{cur_c:,.1f}",
        "Δ LR":          _fmt_d(delta_lr),
        "Δ LY":          _fmt_d(delta_ly),
        "Δ 5YA":         _fmt_d(delta_5ya),
    })

if delta_rows:
    import pandas as pd
    delta_df = pd.DataFrame(delta_rows).set_index("Attribute")
    delta_cols = ["Δ LR", "Δ LY", "Δ 5YA"]
    st.dataframe(
        delta_df.style
                .map(_color_delta, subset=delta_cols)
                .set_properties(**{"font-size": "0.86rem"}),
        use_container_width=True,
        height=min(52 + len(delta_rows) * 36, 400),
    )
    last_mo_lbl = MONTH_LABELS.get(last_mo, str(last_mo))
    st.caption(
        f"Units: {ul}  ·  "
        f"Δ LR = revision in {last_mo_lbl} {last_yr} WASDE  ·  "
        f"Δ LY = vs {mkt_year-1}/{str(mkt_year)[-2:]}  ·  "
        f"Δ 5YA = vs {mkt_year-5}–{mkt_year-1} avg"
    )

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
            pivot_rev.style.map(_color_rev).format("{:+,.1f}", na_rep="—"),
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

style.disclaimer()
