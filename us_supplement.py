"""U.S.-only rows that USDA's PSD feed doesn't carry, added to the Balance Sheet: planted acres (NASS) and, for corn, ethanol.

Both live in JSA.WASDE.US_PROGRESSION (etl/load_nass_planted.py and etl/fetch_us_progression.py) in WASDE's own units and
are converted here to the page's base units (1,000 ha / 1,000 MT) so they follow the Metric / Imperial switch."""
from __future__ import annotations

import pandas as pd

import db
import psd_meta as pm

# page commodity -> name in US_PROGRESSION
NAME = {"Corn": "Corn", "Wheat": "Wheat", "Soybeans": "Soybeans", "Barley": "Barley", "Oats": "Oats", "Sorghum": "Sorghum",
        "Rice, Milled": "Rice", "Cotton": "Cotton", "Canola (Rapeseed)": "Canola", "Sunflowerseed": "Sunflowerseed",
        "Peanuts": "Peanuts"}


def _current(d: pd.DataFrame, item: str) -> dict[int, float]:
    """Each marketing year's current value of an item: the latest USDA release, else its stored 'current' value."""
    x = d[d["item"] == item].copy()
    if x.empty:
        return {}
    x["release_date"] = pd.to_datetime(x["release_date"])
    out: dict[int, float] = {}
    for my, g in x.groupby("market_year"):
        live = g[(g["kind"] == "release") & (g["source"] != "excel")]
        if len(live):
            out[int(my)] = float(live.sort_values("release_date")["value"].iloc[-1])
            continue
        cur = g[g["kind"] == "current"]
        if len(cur):
            nass_first = cur.sort_values("source", key=lambda s: s != "nass")
            out[int(my)] = float(nass_first["value"].iloc[0])
    return out


def add(N: pd.DataFrame, commodity: str) -> pd.DataFrame:
    """N (derived basics, base units) plus 'area_planted' and, for corn, 'ethanol' columns when the data exists."""
    name = NAME.get(commodity)
    if not name or N.empty:
        return N
    try:
        d = db.us_progression(name)
    except Exception:                                    # table unreadable: leave the page as it was
        return N
    if d.empty:
        return N
    N = N.copy()
    planted = _current(d, "Planted Area")
    if planted:                                          # million acres -> 1,000 ha
        N["area_planted"] = pd.Series({y: v * 1000 / pm.ACRES_PER_HA for y, v in planted.items()}).reindex(N.index)
    if commodity == "Corn":
        eth = _current(d, "Ethanol")
        f = pm.imperial_spec("Corn")["qty_factor"]
        if eth and f:                                    # million bushels -> 1,000 MT
            N["ethanol"] = pd.Series({y: v / f for y, v in eth.items()}).reindex(N.index)
    return N
