"""U.S. supply & use progression by WASDE release, laid out like JSA's 'WASDE Progression.xlsx' template.

One block per marketing year: the estimate at each monthly WASDE (May of the year it starts through the following
April) plus 'Current' (latest report, or the final value for older years). Each value is coloured by its change from
the previous release. Data: JSA.WASDE.US_PROGRESSION (etl/fetch_us_progression.py).
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db
import export_buttons
import sd_table
import style

HA_PER_AC = 0.404685642
T_PER_BU = {"Corn": 0.0254016, "Wheat": 0.0272155, "Soybeans": 0.0272155}     # 56 lb and 60 lb bushels

# (item, label, kind): kind decides decimals and the metric conversion
_COMMON_TOP = [("Harvested Area", "Harvested Area", "area"), ("Yield", "Yield", "yield"),
               ("Beginning Stocks", "Beginning Stocks", "qty"), ("Production", "Production", "qty"),
               ("Imports", "Imports", "qty"), ("Total Supply", "Total Supply", "qty")]
_COMMON_END = [("Domestic Consumption", "Domestic Consumption", "qty"), ("Total Demand", "Total Demand", "qty"),
               ("Ending Stocks", "Ending Stocks", "qty"), ("Stocks/Use", "Stocks/Use", "pct")]
ROWS = {
    "Corn": _COMMON_TOP + [("Exports", "Exports", "qty"), ("Feed", "Feed & Residual", "qty"),
                           ("FSI", "Food, Seed & Industrial", "qty")] + _COMMON_END,
    "Wheat": _COMMON_TOP + [("Exports", "Exports", "qty"), ("Feed", "Feed & Residual", "qty"),
                            ("FSI", "Food, Seed & Industrial", "qty")] + _COMMON_END,
    "Soybeans": _COMMON_TOP + [("Crush", "Crush", "qty"), ("Exports", "Exports", "qty")] + _COMMON_END,
}
BOLD = {"Total Supply", "Total Demand", "Ending Stocks"}
_MONTH_ORDER = [(0, m) for m in range(5, 13)] + [(1, m) for m in range(1, 5)]       # May .. Apr


def _columns(my: int) -> list[tuple[int, int]]:
    return [(my + dy, m) for dy, m in _MONTH_ORDER]


def _label(ym: tuple[int, int]) -> str:
    return date(ym[0], ym[1], 1).strftime("%b-%y")


def build(commodity: str, my: int) -> tuple[pd.DataFrame, list[str], dict]:
    """(long frame for this year, ordered column labels, {item: {label: value}}) in WASDE's own units."""
    d = db.us_progression(commodity)
    if d.empty:
        return d, [], {}
    d = d[d.market_year == my].copy()
    if d.empty:
        return d, [], {}
    d["release_date"] = pd.to_datetime(d["release_date"])
    d["_pri"] = (d["source"] == "excel").astype(int)                  # USDA's own file beats the workbook
    d = d.sort_values("_pri").drop_duplicates(["kind", "release_date", "item"], keep="first")
    cols = _columns(my)
    labels = [_label(c) for c in cols] + ["Current"]
    vals: dict[str, dict[str, float]] = {}
    rel = d[d.kind == "release"]
    for r in rel.itertuples():
        key = (r.release_date.year, r.release_date.month)
        if key in cols:
            vals.setdefault(r.item, {})[_label(key)] = float(r.value)
    for item in rel.item.unique():                                     # Current: latest USDA report, else the workbook's
        live = rel[(rel.item == item) & (rel.source != "excel")]
        if len(live):
            vals.setdefault(item, {})["Current"] = float(live.sort_values("release_date").value.iloc[-1])
            continue
        cur = d[(d.kind == "current") & (d.item == item)]
        if len(cur):
            vals.setdefault(item, {})["Current"] = float(cur.value.iloc[0])
    for item in d[d.kind == "current"].item.unique():
        if item not in vals or "Current" not in vals[item]:
            vals.setdefault(item, {})["Current"] = float(d[(d.kind == "current") & (d.item == item)].value.iloc[0])
    return d, labels, vals


def _convert(v: float, kind: str, commodity: str, metric: bool) -> float:
    if not metric or v is None:
        return v
    if kind == "qty":
        return v * T_PER_BU[commodity] * 1000                         # million bu -> 1,000 MT
    if kind == "area":
        return v * HA_PER_AC                                          # million acres -> million ha
    if kind == "yield":
        return v * T_PER_BU[commodity] / HA_PER_AC                    # bu/ac -> t/ha
    return v


@st.fragment
def show(commodity: str, my: int, colors: dict):
    if commodity not in ROWS:
        return
    style.sec(f"U.S. {commodity} — supply & use by WASDE release")
    units = st.segmented_control("Units", ["Bushels / acres", "Metric"], default="Bushels / acres",
                                 key=f"usp_units_{commodity}") or "Bushels / acres"
    metric = units == "Metric"
    d, labels, vals = build(commodity, my)
    if not vals:
        st.info(f"No U.S. {commodity} progression for {my}/{str(my + 1)[-2:]}: history starts with 2011/12.")
        return

    u = {"qty": "1,000 MT" if metric else "million bu", "area": "M ha" if metric else "M ac",
         "yield": "t/ha" if metric else "bu/ac", "pct": "%"}
    rows = []
    for item, name, kind in ROWS[commodity]:
        if item not in vals:
            continue
        conv = {k: _convert(v, kind, commodity, metric) for k, v in vals[item].items()}
        dec = {"area": 2 if metric else 1, "yield": 2 if metric else 1, "qty": 0, "pct": 1}[kind]
        rows.append((f"{name} ({u[kind]})" if kind in ("area", "yield") else name, "pct" if kind == "pct" else "qty",
                     conv, dec))

    heading = f"{my}/{str(my + 1)[-2:]} marketing year"
    unit_txt = "WASDE units: million bushels" if not metric else "1,000 MT"
    u_line = f"Units: {'1,000 MT · area M ha · yield t/ha' if metric else 'million bushels · area million acres · yield bu/ac'}"
    st.markdown(sd_table.render(rows, labels, "Current", unit_txt, colors, trend=True,
                                bold={"Total Supply", "Total Demand", "Ending Stocks"}, heading=heading,
                                title=(f"United States {commodity} — Supply & Demand by WASDE Release",
                                       f"{u_line} · {my}/{str(my + 1)[-2:]} marketing year")),
                unsafe_allow_html=True)
    export_buttons.image_buttons("table", "", f"US_{commodity}_WASDE_progression_{my}_{str(my + 1)[-2:]}.png")

    have = sorted({_label((x.year, x.month)) for x in d[d.kind == "release"].release_date})
    blank = [lb for lb in labels[:-1] if lb not in have]
    note = ("Each value is colored by its change from the previous report (green up, red down); Current is the latest "
            "WASDE (the final estimate for older years). ")
    if blank:
        note += f"Blank columns ({', '.join(blank)}) have no report yet or none was published that month."
    st.caption(note + " Source: USDA WASDE; history 2011/12–2024/25 from JSA's WASDE Progression workbook, newer "
               "releases from USDA's monthly WASDE files.")

    wide = pd.DataFrame({r[0]: {lb: r[2].get(lb) for lb in labels} for r in rows}).T
    st.download_button("Download this year (CSV)", wide.to_csv().encode("utf-8"),
                       file_name=f"US_{commodity}_WASDE_progression_{my}_{str(my + 1)[-2:]}.csv", mime="text/csv",
                       key=f"usp_dl_{commodity}_{my}")


# ── Seasonal overlay: one line per marketing year, by WASDE release ───────────────────────────────────────────
# Year colours follow the Excel chart: older years in a mixed palette, then the last three years stand out.
_MY_COLORS = ["#d98880", "#7fa3d9", "#0b1f3a", "#2f6f7a", "#5b3d73", "#5a6f2a", "#cc1111", "#25476a",
              "#f0903a", "#3fb0c9", "#8061a8", "#9bbf55", "#f5c000"]
_WINDOWS = [(6, "Nov"), (10, "Mar"), (12, "Current")]            # separator positions (x index) on the 13-point axis
_CORN_WINDOW_LABELS = [(2.8, "CZ:CH"), (7.8, "CH:CK"), (11.0, "CK:CN"), (12.65, "CN:CU")]
SEASONAL_ITEMS = ["Stocks/Use", "Production", "Ending Stocks", "Total Demand", "Exports", "Beginning Stocks", "Total Supply",
                  "Yield", "Harvested Area", "Crush", "Imports", "Feed", "FSI", "Domestic Consumption"]


def season_matrix(commodity: str, item: str) -> tuple[dict, date | None]:
    """{marketing year: [12 release values May..Apr + Current]} (None where missing) and the latest release date."""
    d = db.us_progression(commodity)
    if d.empty:
        return {}, None
    d = d[d["item"] == item].copy()
    if d.empty:
        return {}, None
    d["release_date"] = pd.to_datetime(d["release_date"])
    d["_pri"] = (d["source"] == "excel").astype(int)
    d = d.sort_values("_pri").drop_duplicates(["market_year", "kind", "release_date"], keep="first")
    rel_all = d[d["kind"] == "release"]
    latest = rel_all["release_date"].max()
    out = {}
    for my, g in d.groupby("market_year"):
        cols = _columns(int(my))
        vals = [None] * 13
        rel = g[g["kind"] == "release"]
        for r in rel.itertuples():
            key = (r.release_date.year, r.release_date.month)
            if key in cols:
                vals[cols.index(key)] = float(r.value)
        live = rel[rel["source"] != "excel"]
        if len(live):
            cur = float(live.sort_values("release_date")["value"].iloc[-1])
        else:
            c = g[g["kind"] == "current"]
            cur = float(c["value"].iloc[0]) if len(c) else None
        if latest is not None and pd.Timestamp(int(my) + 1, 4, 1) <= latest:      # April report is out: year is complete
            vals[12] = cur
        out[int(my)] = vals
    return out, (latest.date() if latest is not None else None)


@st.fragment
def show_seasonal(commodity: str):
    """Seasonal overlay chart (Stocks-to-Use by default) over the marketing years since 2011/12."""
    if commodity not in ROWS:
        return
    avail = {it: (lab, kind) for it, lab, kind in ROWS[commodity]}
    items = [i for i in SEASONAL_ITEMS if i in avail]
    style.sec(f"Seasonal progression by marketing year — {commodity}")
    c1, c2, c3 = st.columns([1.6, 1.2, 1.6])
    with c1:
        item = st.selectbox("Line item", items, index=0, key=f"seas_item_{commodity}",
                            format_func=lambda i: "Stocks-to-Use" if i == "Stocks/Use" else avail[i][0])
    mat, latest = season_matrix(commodity, item)
    if not mat:
        st.info(f"No U.S. {commodity} progression history loaded.")
        return
    years = sorted(mat)
    with c2:
        first = st.selectbox("From marketing year", years[::-1], index=len(years) - 1, key=f"seas_from_{commodity}",
                             format_func=lambda y: f"{y}/{str(y + 1)[-2:]}")
    corn = commodity == "Corn"
    with c3:
        marks = st.toggle("Show futures-window labels (CZ:CH …)", value=True, key="seas_win_corn") if corn else False

    metric = st.session_state.get(f"usp_units_{commodity}", "Bushels / acres") == "Metric"
    kind = avail[item][1]
    ulab = {"qty": "1,000 MT" if metric else "Million bu", "area": "M ha" if metric else "M ac",
            "yield": "t/ha" if metric else "bu/ac", "pct": "%"}[kind]
    latest_my = max(years)
    x_labels = [_label(c) for c in _columns(latest_my)] + ["Current"]
    show_years = [y for y in years if y >= first]

    fig = go.Figure()
    for y in show_years:
        vals = [None if v is None else _convert(v, kind, commodity, metric) for v in mat[y]]
        if not any(v is not None for v in vals):
            continue
        name = f"{y}/{str(y + 1)[-2:]}"
        age = latest_my - y
        if age == 0:                                                   # year in progress: dotted brown
            line = dict(color="#8a4b08", width=4, dash="dot")
        elif age == 1:                                                 # last year: heavy black dashes
            line = dict(color="#000000", width=6, dash="dash")
        elif age == 2:                                                 # the year before: heavy peach
            line = dict(color="#fbbf8a", width=6)
        else:
            line = dict(color=_MY_COLORS[(y - years[0]) % len(_MY_COLORS)], width=2.5)
        fig.add_trace(go.Scatter(x=x_labels, y=vals, name=name, mode="lines", line=line, connectgaps=True,
                                 hovertemplate=f"<b>{name}</b> · %{{x}}: %{{y:,.1f}} {ulab}<extra></extra>"))
    if marks:
        for idx, _lab in _WINDOWS:
            fig.add_vline(x=idx, line_color="#9aa0a6", line_width=3, opacity=0.85)
        for xpos, txt in _CORN_WINDOW_LABELS:
            fig.add_annotation(x=xpos, y=1.0, yref="paper", yanchor="top", text=f"<b>{txt}</b>", showarrow=False,
                               font=dict(size=13, color="#6b7a70"))
    unit_txt = "%" if kind == "pct" else ulab
    nice = "Stocks-to-Use" if item == "Stocks/Use" else avail[item][0]
    yaxis = dict(title=unit_txt, gridcolor=style.BORDER, tickfont=dict(color="#1f7a3d", size=12))
    yaxis.update({"ticksuffix": "%", "tickformat": ".1f"} if kind == "pct" else {"tickformat": ",.0f"})
    fig.update_layout(**style.plot_layout(
        title=dict(text=f"<b>United States {commodity} — Seasonal {nice} Changes</b>",
                   font=dict(size=17, color="#1f7a3d"), x=0.5, xanchor="center"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="center", x=0.5),
        xaxis=dict(title="", tickfont=dict(color="#1f7a3d", size=12), showgrid=False, range=[-0.4, 13.1]),
        yaxis=yaxis,
        margin=dict(l=10, r=10, t=110, b=45), height=540))
    export_buttons.plotly_chart(fig, width="stretch", key=f"seas_chart_{commodity}_{item}".replace("/", "_"),
                                filename=f"US_{commodity}_seasonal_{item.replace('/', '_')}")
    st.caption("Each line is one marketing year, plotted at every monthly WASDE from May through April plus the current "
               "estimate (shown once the April report is out). The dotted line is the year in progress, the heavy dashed "
               "line last year. " + ("The grey dividers mark the corn futures spread windows. " if marks else "")
               + f"Units: {ulab}.")
    table = pd.DataFrame({f"{y}/{str(y + 1)[-2:]}": [None if v is None else _convert(v, kind, commodity, metric)
                                                      for v in mat[y]] for y in show_years}, index=x_labels)
    st.download_button("Download chart data (CSV)", table.to_csv().encode("utf-8"),
                       file_name=f"US_{commodity}_seasonal_{item.replace('/', '_')}.csv", mime="text/csv",
                       key=f"seas_dl_{commodity}_{item}".replace("/", "_"))
