"""U.S. supply & use progression by WASDE release, laid out like JSA's 'WASDE Progression.xlsx' template.

One block per marketing year: the estimate at each monthly WASDE (May of the year it starts through the following
April) plus 'Current' (latest report, or the final value for older years). Each value is coloured by its change from
the previous release. Data: JSA.WASDE.US_PROGRESSION (etl/fetch_us_progression.py).
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

import db
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
    st.markdown(sd_table.render(rows, labels, "Current", unit_txt, colors, trend=True,
                                bold={"Total Supply", "Total Demand", "Ending Stocks"}, heading=heading),
                unsafe_allow_html=True)

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
