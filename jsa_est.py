"""JSA's own balance-sheet estimates beside USDA's, plus the change-vs-LY / 5-year-average columns for the table.

Values are stored in the table's base units (the same as PSD: 1,000 MT, 1,000 ha, t/ha) so they follow the
Metric / Imperial switch; the entry form converts what is typed back with the same linear factors."""
from __future__ import annotations

import numpy as np
import pandas as pd

import balance_sheet as bs
import db
import psd_meta as pm

DERIVED = {"total_supply", "total_use", "stu"}


def factor(key: str, commodity: str, system: str) -> float:
    """Display units per base unit (all three conversions are linear)."""
    kind = bs.KIND[key]
    if kind == "qty":
        return pm.qty_out(commodity, 1.0, system)
    if kind == "area":
        return pm.area_out(1.0, system)
    if kind == "yield":
        return pm.yield_out(commodity, 1.0, system)
    return 1.0


def load(region: str, commodity: str) -> dict[int, dict[str, float]]:
    try:
        d = db.jsa_estimates(region, commodity)
    except Exception:                       # table not created yet
        return {}
    out: dict[int, dict[str, float]] = {}
    for r in d.itertuples():
        if r.value is not None and not pd.isna(r.value):
            out.setdefault(int(r.market_year), {})[r.item] = float(r.value)
    return out


def _derive(j: dict) -> dict:
    j = dict(j)
    if "total_supply" not in j and "beginning_stocks" in j and "production" in j:
        j["total_supply"] = j["beginning_stocks"] + j["production"] + j.get("imports", 0.0)
    if "total_use" not in j and "domestic" in j and "exports" in j:
        j["total_use"] = j["domestic"] + j["exports"]
    if "ending_stocks" in j and j.get("total_use", 0) > 0:
        j["stu"] = j["ending_stocks"] / j["total_use"] * 100
    return j


def jsa_column(j: dict, year: int, keys: list[str], commodity: str, system: str) -> dict:
    """{row key: display value} for the JSA column (empty when nothing was entered)."""
    if not j:
        return {}
    j = _derive(j)
    frame = pd.DataFrame({k: [j.get(k, np.nan)] for k in keys}, index=[year])
    disp = bs.to_display(frame, commodity, system, keys)
    return {k: (None if pd.isna(disp.at[year, k]) else float(disp.at[year, k])) for k in keys}


def _num(s: pd.Series, k: str):
    return None if (k not in s or pd.isna(s[k])) else float(s[k])


def extra_columns(D: pd.DataFrame, year: int, keys: list[str], jsa: dict, label: str) -> list:
    """Columns after the newest year: JSA estimate, change vs last year, 5-year average, change vs that average."""
    full = D.reindex(range(int(D.index.min()), int(D.index.max()) + 1))
    cur = full.loc[year]
    prev5 = full.loc[[y for y in range(year - 5, year) if y in full.index]]
    ly = full.loc[year - 1] if (year - 1) in full.index else pd.Series(dtype=float)
    avg, cnt = prev5.mean(), prev5.notna().sum()

    d_ly, a5, d_av = {}, {}, {}
    for k in keys:
        c, l = _num(cur, k), _num(ly, k)
        d_ly[k] = None if c is None or l is None else c - l
        a5[k] = _num(avg, k) if cnt.get(k, 0) >= 3 else None
        d_av[k] = None if c is None or a5[k] is None else c - a5[k]
    return [(f"JSA Est. {label}", jsa, "jsa"), ("Δ vs LY", d_ly, "delta"), ("5-Yr Avg", a5, "avg"),
            ("Δ vs Avg", d_av, "delta")]
