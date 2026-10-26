"""Standard supply-and-demand balance sheet built from USDA PSD history.

Pipeline: raw PSD rows -> `basics` (base units) -> `derive` (totals, stocks-to-use) -> a view
(current / change vs last report / vs last year / vs 5-yr avg, all in base units) -> display units -> HTML.
Quantities are carried in 1,000 MT, area in 1,000 ha and yield in MT/ha until the last step, so every
difference is computed before conversion (all conversions are linear).
"""

from __future__ import annotations

import html

import numpy as np
import pandas as pd

import psd_meta as pm

# key, label, group, kind, style
ROWS = [
    ("area_planted",     "Area Planted",            "Area & Yield", "area",  ""),
    ("area_harvested",   "Area Harvested",          "Area & Yield", "area",  ""),
    ("yield",            "Yield",                   "Area & Yield", "yield", ""),
    ("beginning_stocks", "Beginning Stocks",        "Supply",       "qty",   ""),
    ("production",       "Production",              "Supply",       "qty",   ""),
    ("imports",          "Imports",                 "Supply",       "qty",   ""),
    ("total_supply",     "Total Supply",            "Supply",       "qty",   "total"),
    ("crush",            "Crush",                   "Demand",       "qty",   ""),
    ("feed",             "Feed & Residual",         "Demand",       "qty",   ""),
    ("feed_waste",       "Feed & Waste",            "Demand",       "qty",   ""),
    ("food_use",         "Food Use",                "Demand",       "qty",   ""),
    ("fsi",              "Food, Seed & Industrial", "Demand",       "qty",   ""),
    ("industrial",       "Industrial Use",          "Demand",       "qty",   ""),
    ("mill_use",         "Mill Use",                "Demand",       "qty",   ""),
    ("loss",             "Loss",                    "Demand",       "qty",   ""),
    ("domestic",         "Domestic Consumption",    "Demand",       "qty",   "sub"),
    ("exports",          "Exports",                 "Demand",       "qty",   ""),
    ("total_use",        "Total Use",               "Demand",       "qty",   "total"),
    ("ending_stocks",    "Ending Stocks",           "Stocks",       "qty",   "total"),
    ("stu",              "Stocks-to-Use",           "Stocks",       "pct",   "ratio"),
]
KIND = {r[0]: r[3] for r in ROWS}
LABEL = {r[0]: r[1] for r in ROWS}

# PSD attribute id behind each basic row (domestic is derived from 125 or 142)
ATTR_FOR = {
    "area_planted": 1, "area_harvested": 4, "yield": 184, "beginning_stocks": 20, "production": 28,
    "imports": 57, "total_supply": 86, "crush": 7, "feed": 130, "feed_waste": 161, "food_use": 149,
    "fsi": 192, "industrial": 140, "loss": 150, "exports": 88, "ending_stocks": 176,
    "dom125": 125, "dom142": 142, "dom126": 126,
}

# WASDE snapshot attribute name -> basic row key (used for the change-vs-last-report view)
SNAPSHOT_KEY = {
    "Beginning Stocks": "beginning_stocks", "Production": "production", "Imports": "imports",
    "Total Supply": "total_supply", "Feed/Residual Use": "feed", "Food/Seed/Industrial Use": "fsi",
    "Exports": "exports", "Ending Stocks": "ending_stocks", "Total Consumption": "dom125",
    "Area Harvested": "area_harvested",
}

VIEWS = {"Current": "current", "Δ LR": "lr", "Δ LY": "ly", "Δ 5YA": "5ya"}


def _kind(key: str) -> str:
    return KIND.get(key) or ("area" if key.startswith("area") else "yield" if key == "yield" else "qty")


def basics(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot raw PSD rows to base units: index market_year, one column per ATTR_FOR key."""
    if df.empty:
        return pd.DataFrame()
    unit = df.groupby("attribute_id")["unit_id"].agg(lambda s: s.mode().iat[0])
    pv = df.pivot_table(index="market_year", columns="attribute_id", values="value", aggfunc="first").sort_index()
    out = pd.DataFrame(index=pv.index)
    for key, aid in ATTR_FOR.items():
        if aid not in pv.columns:
            continue
        kind = _kind(key)
        if kind == "qty":
            out[key] = pv[aid].map(lambda v, u=unit[aid]: pm.kt_from_raw(v, u))
        elif kind == "yield":
            out[key] = pv[aid].map(lambda v, u=unit[aid]: pm.t_ha_from_raw(v, u))
        else:
            out[key] = pv[aid]
    return out


def derive(b: pd.DataFrame) -> pd.DataFrame:
    """Add domestic consumption, total use, stocks-to-use and a computed yield where USDA has none."""
    n = b.copy()
    if "dom125" not in n and "dom126" in n:          # sugar: Domestic Consumption is attribute 126
        n["dom125"] = n["dom126"]
    if "dom125" in n:
        n["domestic"] = n["dom125"]
    elif "dom142" in n:
        n["domestic"] = n["dom142"] + (n["loss"].fillna(0) if "loss" in n else 0)
    if "domestic" in n and "exports" in n:
        n["total_use"] = n["domestic"] + n["exports"]
    if "total_use" in n and "ending_stocks" in n:
        n["stu"] = (n["ending_stocks"] / n["total_use"].where(n["total_use"] > 0) * 100)
    if "yield" not in n and "production" in n and "area_harvested" in n:
        n["yield"] = n["production"] / n["area_harvested"].where(n["area_harvested"] > 0)
    if "dom125" not in n and "dom142" in n:          # cotton: consumption is reported as 'Domestic Use'
        n["mill_use"] = n["dom142"]
    return n.drop(columns=[c for c in ("dom125", "dom126", "dom142") if c in n])


def previous_vintage(b: pd.DataFrame, rev: pd.DataFrame, units: pd.Series) -> pd.DataFrame:
    """Basics as they stood at the previous WASDE: current minus the latest revision (NaN where unknown)."""
    p = pd.DataFrame(np.nan, index=b.index, columns=b.columns)
    if rev is None or rev.empty:
        return p
    for r in rev.itertuples():
        key = SNAPSHOT_KEY.get(r.attribute_name)
        if key is None or key not in b.columns or r.market_year not in b.index or pd.isna(r.revision):
            continue
        aid = ATTR_FOR[key]
        raw_unit = units.get(aid)
        change = pm.kt_from_raw(r.revision, raw_unit) if _kind(key) == "qty" else r.revision
        p.loc[r.market_year, key] = b.loc[r.market_year, key] - change
    return p


def make_view(n: pd.DataFrame, view: str, prev: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return the table values for a view, in base units."""
    full = n.reindex(range(int(n.index.min()), int(n.index.max()) + 1))
    if view == "current":
        out = full
    elif view == "ly":
        out = full - full.shift(1)
    elif view == "5ya":
        out = full - full.shift(1).rolling(5, min_periods=3).mean()
    else:  # "lr"
        if prev is None:
            return full * np.nan
        p = derive(prev).reindex(full.index)
        out = full - p
        if {"production", "area_harvested"} <= set(full.columns):     # yield change from production / area
            out["yield"] = (full["production"] / full["area_harvested"].where(full["area_harvested"] > 0)
                            - p["production"] / p["area_harvested"].where(p["area_harvested"] > 0))
    return out


OILSEEDS = {"Soybeans", "Canola (Rapeseed)", "Sunflowerseed", "Peanuts"}
OPTIONAL = {"area_planted", "crush", "feed", "feed_waste", "food_use", "fsi", "industrial", "loss"}


def visible_keys(n: pd.DataFrame, years: list[int], commodity: str | None = None) -> list[str]:
    """Rows with data in the range; optional rows that are all zero are hidden, and Crush shows only on oilseeds."""
    sub = n.loc[n.index.isin(years)]
    keys = []
    for k, *_ in ROWS:
        if k not in sub.columns or not sub[k].notna().any():
            continue
        if k in OPTIONAL and not (sub[k].fillna(0).abs() > 1e-9).any():
            continue
        if k == "crush" and commodity is not None and commodity not in OILSEEDS:
            continue
        keys.append(k)
    return keys


def to_display(v: pd.DataFrame, commodity: str, system: str, keys: list[str]) -> pd.DataFrame:
    d = pd.DataFrame(index=v.index)
    for k in keys:
        kind = KIND[k]
        col = v[k]
        if kind == "qty":
            d[k] = col.map(lambda x: pm.qty_out(commodity, None if pd.isna(x) else x, system))
        elif kind == "area":
            d[k] = col.map(lambda x: pm.area_out(None if pd.isna(x) else x, system))
        elif kind == "yield":
            d[k] = col.map(lambda x: pm.yield_out(commodity, None if pd.isna(x) else x, system))
        else:
            d[k] = col
    return d


def _decimals(key: str, rowmax: float, commodity: str, system: str) -> int:
    kind = KIND[key]
    if kind == "qty":
        if system == "metric":
            return 0 if rowmax >= 1000 else 1
        return 0 if (rowmax >= 10000 and pm.imperial_spec(commodity)["bu_per_mt"] is None) else 1
    if kind == "area":
        return 2 if rowmax < 10 else 1
    if kind == "yield":
        return 0 if rowmax >= 100 else 2
    return 1


def _fmt(x, dec: int, signed: bool, pct: bool) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    if signed and round(x, dec) == 0:
        return f"{0:.{dec}f}" + ("pp" if pct else "")
    s = f"{x:+,.{dec}f}" if signed else f"{x:,.{dec}f}"
    return s + ("pp" if (pct and signed) else "%" if pct else "")


def render_html(d: pd.DataFrame, years: list[int], keys: list[str], commodity: str, system: str,
                view: str, latest_year: int, colors: dict, title: tuple[str, str] | None = None,
                extra: list | None = None) -> str:
    """Grouped balance-sheet table, oldest year on the left and the newest on the right. `d` is in display units.

    extra = [(header, {row key: value}, kind)] adds columns after the newest year (kind 'jsa' | 'delta' | 'avg').

    title = (headline, units line) draws a title bar inside the card above the header row, so the table is
    self-explanatory when it is copied or saved as a picture."""
    units = pm.unit_labels(commodity, system)
    signed = view != "current"
    years_desc = sorted(years)                      # name kept from the old newest-first order; now oldest first
    extra = extra or []
    dec = {k: _decimals(k, float(np.nanmax(np.abs(d.loc[d.index.isin(years), k].astype(float).values)))
                        if d.loc[d.index.isin(years), k].notna().any() else 0.0, commodity, system)
           for k in keys}

    head = "".join(
        f"<th class='{'cur' if y == latest_year else ''}'>{y}/{str(y + 1)[-2:]}</th>" for y in years_desc)
    head += "".join(f"<th class='ex {kd}'>{html.escape(h)}</th>" for h, _, kd in extra)
    ncols = len(years_desc) + len(extra)
    body = []
    last_group = None
    for k in keys:
        _, label, group, kind, style = next(r for r in ROWS if r[0] == k)
        if group != last_group:
            gunit = f"<span class='gu'>· {html.escape(units['qty'])}</span>" if group != "Area & Yield" else ""
            body.append(f"<tr class='grp'><td colspan='{ncols + 1}'>{html.escape(group)} {gunit}</td></tr>")
            last_group = group
        unit = units.get(kind, "") if kind in ("area", "yield") else ("%" if kind == "pct" else "")
        cells = []
        for y in years_desc:
            x = d.at[y, k] if (y in d.index) else np.nan
            cls = "cur" if y == latest_year else ""
            if signed and isinstance(x, float) and not np.isnan(x) and round(x, dec[k]) != 0:
                cls += " pos" if x > 0 else " neg"
            cells.append(f"<td class='{cls.strip()}'>{_fmt(x, dec[k], signed, kind == 'pct')}</td>")
        for _h, vals, kd in extra:
            x = vals.get(k)
            x = np.nan if x is None else x
            sg = kd == "delta"
            cls = f"ex {kd}"
            if sg and not np.isnan(x) and round(x, dec[k]) != 0:
                cls += " pos" if x > 0 else " neg"
            cells.append(f"<td class='{cls}'>{_fmt(x, dec[k] if kd != 'jsa' else dec[k], sg, kind == 'pct')}</td>")
        body.append(f"<tr class='{style}'><th class='lab'>{html.escape(label)}"
                    f"{(' <span class=u>' + html.escape(unit) + '</span>') if unit else ''}</th>{''.join(cells)}</tr>")

    css = (
        "<style>"
        ".bs-wrap{direction:rtl;overflow-x:auto;border:1px solid %(border)s;border-radius:10px;background:#fff}"
        ".bs-title{direction:ltr;position:sticky;left:0;padding:14px 16px 10px;background:#fff;border-bottom:3px solid %(green)s}"
        ".bs-title .t1{font:700 1.4rem/1.15 'EB Garamond',Georgia,serif;color:%(text)s}"
        ".bs-title .t2{font:700 0.95rem 'Source Sans 3','Source Sans Pro',Arial,sans-serif;color:%(green)s;margin-top:3px}"
        "table.bs{direction:ltr;border-collapse:separate;border-spacing:0;width:100%%;font-size:0.88rem;"
        "font-variant-numeric:tabular-nums;color:%(text)s}"
        "table.bs thead th{position:sticky;top:0;background:%(green)s;color:#fff;font-weight:600;"
        "padding:8px 12px;text-align:right;white-space:nowrap}"
        "table.bs thead th:first-child{left:0;z-index:3;text-align:left;min-width:210px;border-top-left-radius:9px}"
        "table.bs thead th.cur{background:%(dark)s}"
        "table.bs td,table.bs th.lab{padding:6px 12px;text-align:right;white-space:nowrap;"
        "border-bottom:1px solid %(border)s}"
        "table.bs th.lab{position:sticky;left:0;background:#fff;text-align:left;font-weight:500;z-index:1}"
        "table.bs .u{font-size:0.7rem;font-weight:400;color:%(muted)s;margin-left:4px}""table.bs .gu{font-weight:400;letter-spacing:0;text-transform:none;font-size:0.72rem;color:%(muted)s;margin-left:4px}"
        "table.bs td.cur{background:#fbf7e8}"
        "table.bs thead th.ex{background:#4a5a52}table.bs thead th.jsa{background:#0b5cab}"
        "table.bs td.ex{border-left:1px solid %(border)s}table.bs td.jsa{background:#eef5fc;font-weight:600}"
        "table.bs td.avg{background:#f7f7f7}"
        "table.bs tr.grp td{background:#eef3ef;color:%(green)s;font-size:0.7rem;font-weight:700;"
        "letter-spacing:0.12em;text-transform:uppercase;text-align:left;padding:5px 12px}"
        "table.bs tr.total td,table.bs tr.total th.lab{font-weight:700;background:#f4f7f5;"
        "border-top:1.5px solid %(green)s}"
        "table.bs tr.total td.cur{background:#f6f0d8}"
        "table.bs tr.sub td,table.bs tr.sub th.lab{font-weight:600;border-top:1px solid %(green)s}"
        "table.bs tr.ratio td,table.bs tr.ratio th.lab{font-weight:700;color:%(good)s;background:#eef6ee}"
        "table.bs td.pos{color:%(good)s;font-weight:600}table.bs td.neg{color:%(bad)s;font-weight:600}"
        "table.bs tr:last-child td,table.bs tr:last-child th.lab{border-bottom:none}"
        "</style>"
    ) % {"border": colors["border"], "text": colors["text"], "green": colors["green"], "dark": colors["dark"],
         "muted": colors["muted"], "good": colors["good"], "bad": colors["bad"]}
    title_html = ""
    if title:
        title_html = (f"<div class='bs-title'><div class='t1'>{html.escape(title[0])}</div>"
                      f"<div class='t2'>{html.escape(title[1])}</div></div>")
    return (f"{css}<div class='bs-wrap'>{title_html}<table class='bs'><thead><tr><th>Category</th>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def to_csv(d: pd.DataFrame, years: list[int], keys: list[str], commodity: str, system: str,
           extra: list | None = None) -> str:
    units = pm.unit_labels(commodity, system)
    rows = []
    for k in keys:
        kind = KIND[k]
        rows.append({"Category": LABEL[k], "Unit": "%" if kind == "pct" else units[kind],
                     **{f"{y}/{str(y + 1)[-2:]}": d.at[y, k] if y in d.index else np.nan
                        for y in sorted(years)},
                     **{h: vals.get(k) for h, vals, _ in (extra or [])}})
    return pd.DataFrame(rows).to_csv(index=False)
