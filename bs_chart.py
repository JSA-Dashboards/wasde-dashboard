"""Balance Sheet page: a chart you can point at any line item(s) of the table above it.

Pick items (production, exports, ending stocks, stocks-to-use, ...), a chart type and either actual values or an
index. Items with different units share the chart: volumes on the left axis, a second unit (e.g. stocks-to-use %,
area or yield) on the right. More than two unit types switches to an index so every line is comparable.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import balance_sheet as bs
import export_buttons
import style
from style import C_BLUE, C_GOLD, C_GREEN, C_LIME, C_ORANGE, C_RED

PALETTE = [C_GOLD, C_LIME, C_BLUE, C_GREEN, C_ORANGE, "#9b89c4", C_RED, "#2f7d7a", "#8c8c8c", "#d9b87a"]
DEFAULT = ("production", "exports", "stu")


def _unit(kind: str, labels: dict) -> str:
    return {"qty": labels["qty"], "area": labels["area"], "yield": labels["yield"], "pct": "%"}[kind]


@st.fragment
def show(view: pd.DataFrame, keys: list[str], years: list[int], labels: dict, commodity: str, region: str, mkt):
    """view: the 'current' display table (index = marketing year start, one column per row key)."""
    avail = [k for k in keys if k in view and view.loc[view.index.isin(years), k].notna().any()]
    if not avail or not years:
        return
    tag = f"{commodity}_{region}".replace(" ", "_")          # options depend on commodity/region: keep them in the key
    default = [k for k in DEFAULT if k in avail] or avail[:3]

    c1, c2, c3 = st.columns([2.5, 2.0, 1.6])
    with c1:
        sel = st.multiselect("Line items", avail, default=default, format_func=lambda k: bs.LABEL[k],
                             key=f"bsx_items_{tag}")
    with c2:
        mode = st.segmented_control("Chart", ["Lines", "Bars", "Stacked"], default="Lines",
                                    key=f"bsx_mode_{tag}") or "Lines"
    with c3:
        scale = st.segmented_control("Scale", ["Actual", "Index"], default="Actual", key=f"bsx_scale_{tag}") or "Actual"
    if not sel:
        st.info("Pick at least one line item.")
        return

    ys = [y for y in years if y in view.index]
    xs = [mkt(y) for y in ys]
    kinds = list(dict.fromkeys(bs.KIND[k] for k in sel))       # unit types in the order picked
    note = ""
    if scale == "Actual" and len(kinds) > 2:
        scale = "Index"
        note = "More than two unit types are selected, so the lines are shown as an index (first year = 100). "
    left, right = kinds[0], (kinds[1] if len(kinds) > 1 else None)
    indexed = scale == "Index"

    fig = go.Figure()
    stacked = mode == "Stacked" and not indexed
    for i, k in enumerate(sel):
        series = pd.Series([view.at[y, k] for y in ys], dtype=float)
        if indexed:
            base = series.dropna()
            series = series / base.iloc[0] * 100 if len(base) and base.iloc[0] else series
        on_right = (not indexed) and bs.KIND[k] != left
        color = PALETTE[i % len(PALETTE)]
        as_bar = mode in ("Bars", "Stacked") and not on_right
        if as_bar:
            fig.add_trace(go.Bar(x=xs, y=series, name=bs.LABEL[k], marker_color=color, opacity=0.9,
                                 hovertemplate="%{x}: %{y:,.1f}<extra>" + bs.LABEL[k] + "</extra>"))
        else:
            fig.add_trace(go.Scatter(x=xs, y=series, name=bs.LABEL[k],
                                     mode="lines+markers", line=dict(color=color, width=2.5), marker=dict(size=5),
                                     yaxis="y2" if on_right else "y",
                                     hovertemplate="%{x}: %{y:,.1f}<extra>" + bs.LABEL[k] + "</extra>"))

    left_title = "Index (first year = 100)" if indexed else _unit(left, labels)
    layout = dict(
        title=dict(text=f"{region} {commodity} — " + " · ".join(bs.LABEL[k] for k in sel), font=dict(size=13)),
        barmode="stack" if stacked else "group",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
        xaxis=dict(**style._AXIS, nticks=14, tickangle=-45),
        margin=dict(l=10, r=10, t=85, b=45),
        yaxis=dict(title=left_title, gridcolor=style.BORDER),
        height=380,
    )
    if right and not indexed:
        layout["yaxis2"] = dict(title=_unit(right, labels), overlaying="y", side="right",
                                gridcolor="rgba(0,0,0,0)", zeroline=False)
    fig.update_layout(**style.plot_layout(**layout))
    st.plotly_chart(fig, width="stretch", key=f"bsx_chart_{tag}")
    export_buttons.image_buttons("plot", "", f"{tag}_chart.png")

    units = ", ".join(f"{bs.LABEL[k]} in {_unit(bs.KIND[k], labels)}" for k in sel)
    st.caption(f"{note}{commodity} ({region}) over the selected marketing years. "
               + ("Index: each line starts at 100 in the first year shown. " if indexed else f"Units: {units}. ")
               + ("Items in a second unit are drawn on the right axis. " if right and not indexed else "")
               + ("Stacked bars add the volume items together; other units stay as lines. " if stacked else ""))
    data = pd.DataFrame({bs.LABEL[k]: [view.at[y, k] for y in ys] for k in sel}, index=xs)
    data.index.name = "Marketing year"
    st.download_button("Download chart data (CSV)", data.to_csv().encode("utf-8"),
                       file_name=f"{tag}_chart_data.csv", mime="text/csv", key=f"bsx_dl_{tag}")
