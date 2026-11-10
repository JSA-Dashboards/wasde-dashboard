"""USDA's 10-year agricultural baseline (the early-release tables, Nov 2026: projections through 2036/37).

The file in data/ is USDA's own machine-readable table (ERS Agricultural Baseline Database, 'Projections*.csv').
When USDA publishes the next baseline (the full report each February), replace data/baseline_projections.csv
with the new CSV; nothing else changes. Everything is shown in USDA's own units."""
from __future__ import annotations

import pathlib

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import export_buttons
import sd_table
import style

DATA = pathlib.Path(__file__).parent / "data" / "baseline_projections.csv"

# Page commodity -> name in the baseline file
BASELINE_NAME = {"Corn": "Corn", "Wheat": "Wheat", "Soybeans": "Soybeans", "Soybean Meal": "Soybean meal",
                 "Soybean Oil": "Soybean oil", "Barley": "Barley", "Oats": "Oats", "Sorghum": "Sorghum",
                 "Rice, Milled": "Rice", "Cotton": "Upland Cotton"}

# attribute -> (label shown, kind); the table lists whichever of these the commodity has, in this order
_SPEC = [
    ("Planted acres", "Area Planted", "area"), ("Harvested acres", "Area Harvested", "area"),
    ("Yield/harvested acre", "Yield", "yield"),
    ("Beginning stocks", "Beginning Stocks", "qty"), ("Production", "Production", "qty"),
    ("Imports", "Imports", "qty"), ("Supply total", "Total Supply", "qty"),
    ("Crush", "Crush", "qty"), ("Feed & residual", "Feed & Residual", "qty"), ("Food", "Food", "qty"),
    ("Seed", "Seed", "qty"), ("Food, seed, & industrial", "Food, Seed & Industrial", "qty"),
    ("Ethanol for fuel", "  of which Ethanol for Fuel", "qty"), ("Seed & residual", "Seed & Residual", "qty"),
    ("Biofuel", "Biofuel", "qty"), ("Domestic use", "Domestic Use", "qty"), ("Exports", "Exports", "qty"),
    ("Total use/disposition", "Total Use", "qty"), ("Total demand", "Total Demand", "qty"),
    ("Ending stocks", "Ending Stocks", "qty"), ("Stocks/use ratio, percent", "Stocks/Use", "pct"),
    ("Farm price", "Farm Price", "price"), ("Price", "Price", "price"), ("Crush margin", "Crush Margin", "price"),
    ("Crushing yields", "Crushing Yield", "yield"),
]
_BOLD = {"Total Supply", "Total Use", "Total Demand", "Ending Stocks"}
_RELEASE = "November 2026 early release"
_CUR = "2026/27"                                      # the current marketing year (October 2026 WASDE)


@st.cache_data(show_spinner=False)
def _load() -> pd.DataFrame:
    d = pd.read_csv(DATA, encoding="utf-8-sig")
    return d[d["year"].astype(str).str.contains("/")]          # marketing-year series (the file also has calendar-year rows)


def available(commodity: str) -> bool:
    return commodity in BASELINE_NAME and not _load()[lambda d: d.commodity == BASELINE_NAME[commodity]].empty


def _dec(unit: str, kind: str) -> int:
    if kind == "pct":
        return 1
    if kind == "price":
        return 3 if "pound" in unit.lower() else 2
    if kind == "yield":
        return 2 if "per bushel" in unit.lower() else 1
    if kind == "area":
        return 0 if unit.lower().startswith("thousand") else 1
    return 1 if "cwt" in unit.lower() else 0


@st.fragment
def show(commodity: str, colors: dict):
    """Baseline balance sheet for the U.S. commodity: marketing years 2025/26 through 2036/37."""
    if not available(commodity):
        return
    name = BASELINE_NAME[commodity]
    d = _load()
    d = d[d.commodity == name]
    years = sorted(d["year"].unique())
    style.sec(f"USDA 10-year baseline — {commodity}")

    rows, units, seen = [], {}, set()
    for attr, label, kind in _SPEC:
        s = d[d.attribute == attr]
        if s.empty or label in seen:
            continue
        seen.add(label)
        unit = str(s["unit"].iloc[0])
        units[label] = unit
        vals = {r.year: float(r.value) for r in s.itertuples()}
        shown = f"{label.strip()} ({unit.replace('Million ', 'M ').replace('Thousand ', 'K ')})" if kind in ("area", "yield", "price") else label
        rows.append((shown, "pct" if kind == "pct" else "qty", vals, _dec(unit, kind)))
    qty_unit = units.get("Total Supply") or units.get("Production") or ""
    title = f"United States {commodity} — USDA 10-Year Baseline"
    sub = f"Units: {qty_unit} · {years[0]} – {years[-1]} marketing years · {_RELEASE}"
    st.markdown(sd_table.render(rows, years, _CUR, qty_unit, colors, trend=True, bold=_BOLD,
                                heading="Supply & demand projections", title=(title, sub)),
                unsafe_allow_html=True)
    slug = f"US_{commodity}".replace(" ", "_").replace(",", "")
    export_buttons.image_buttons("table", "", f"{slug}_baseline_2036.png")
    st.caption(f"USDA Agricultural Projections to 2036 ({_RELEASE}): 2025/26 is last year's estimate, 2026/27 reflects the "
               "October 2026 WASDE, and 2027/28 onward are USDA's baseline projections, which assume no policy changes or "
               "shocks and are not forecasts of a specific year. Values are shaded green or red by their change from "
               "the prior year. The full report, with more detail, follows in February 2027. "
               + ("Rice is on a rough-rice basis (million cwt) and differs from the milled basis used in PSD. "
                  if commodity == "Rice, Milled" else ""))

    wide = pd.DataFrame({r[0]: {y: r[2].get(y) for y in years} for r in rows}).T
    st.download_button("Download baseline (CSV)", wide.to_csv().encode("utf-8"),
                       file_name=f"US_{commodity}_USDA_baseline_2036.csv".replace(" ", "_"), mime="text/csv",
                       key=f"baseline_dl_{commodity}")

    labels = [r[0] for r in rows]
    pick = st.selectbox("Chart a baseline line", labels, index=labels.index("Production") if "Production" in labels else 0,
                        key=f"baseline_pick_{commodity}")
    row = next(r for r in rows if r[0] == pick)
    fig = go.Figure(go.Scatter(x=years, y=[row[2].get(y) for y in years], mode="lines+markers",
                               line=dict(color=colors["green"], width=3), marker=dict(size=7),
                               hovertemplate="%{x}: %{y:,.1f}<extra></extra>"))
    fig.add_vrect(x0=-0.5, x1=1.5, fillcolor="#fbf7e8", opacity=0.9, line_width=0, layer="below")
    fig.update_layout(**style.plot_layout(
        title=dict(text=f"<b>United States {commodity} — {pick} baseline to {years[-1]}</b>",
                   font=dict(size=17, color="#1f7a3d"), x=0.5, xanchor="center"),
        xaxis=dict(title="", showgrid=False), yaxis=dict(title=qty_unit if row[1] == "qty" else "", gridcolor=style.BORDER),
        margin=dict(l=10, r=10, t=60, b=40), height=380))
    export_buttons.plotly_chart(fig, width="stretch", key=f"baseline_chart_{commodity}_{pick}",
                                filename=f"{slug}_baseline_{pick}".replace(" ", "_").replace("/", "_"))
    st.caption("The shaded band marks the two most recent years of actuals/current estimates.")
