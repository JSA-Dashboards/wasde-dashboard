"""Shared PSD metadata: commodity codes, standard balance-sheet layout and unit conversions.

Used by etl/fetch_psd_history.py (what to load) and the Balance Sheet page (how to show it).
Quantities arrive from USDA in 1,000 MT (cotton: 1,000 480-lb bales), area in 1,000 ha, yield in MT/ha.
"""

from __future__ import annotations

# Display name -> PSD commodity code, in menu order
COMMODITIES: dict[str, str] = {
    "Corn": "0440000",
    "Wheat": "0410000",
    "Soybeans": "2222000",
    "Soybean Meal": "0813100",
    "Soybean Oil": "4232000",
    "Barley": "0430000",
    "Oats": "0452000",
    "Sorghum": "0459200",
    "Rye": "0451000",
    "Millet": "0459100",
    "Rice, Milled": "0422110",
    "Cotton": "2631000",
    "Canola (Rapeseed)": "2226000",
    "Canola Meal": "0813600",
    "Canola Oil": "4239100",
    "Sunflowerseed": "2224000",
    "Sunflowerseed Meal": "0813500",
    "Sunflowerseed Oil": "4236000",
    "Palm Oil": "4243000",
    "Peanuts": "2221000",
    "Sugar": "0612000",
}
CODE_TO_NAME = {v: k for k, v in COMMODITIES.items()}

WORLD = "00"

# PSD attribute ids used by the standard balance sheet
A = {
    "area_planted": 1, "area_harvested": 4, "crush": 7, "beginning_stocks": 20, "production": 28,
    "imports": 57, "total_supply": 86, "exports": 88, "domestic_consumption": 125,
    "feed": 130, "domestic_use": 142, "loss": 150, "ending_stocks": 176, "yield": 184, "fsi": 192,
    "food_use": 149,
}
ATTR_NAME = {v: k for k, v in A.items()}

# Metric-to-imperial factors. 'qty' converts 1,000 MT to the imperial label; area/yield handled separately.
# (label, factor applied to 1,000 MT, bushels per MT or None for the yield conversion)
LB_PER_MT = 2204.62262
# Standard bushel weights (lb): bushels per MT = LB_PER_MT / lb per bushel
_LB_PER_BU = {"Corn": 56, "Sorghum": 56, "Rye": 56, "Millet": 50, "Wheat": 60, "Soybeans": 60,
              "Barley": 48, "Oats": 32, "Canola (Rapeseed)": 50}
_BU = {k: LB_PER_MT / v for k, v in _LB_PER_BU.items()}
ACRES_PER_HA = 2.47105
KG_PER_BALE = 217.724


def imperial_spec(commodity: str) -> dict:
    """Display units for a commodity in each system."""
    if commodity in _BU:
        return {"metric": "1,000 MT", "imperial": "Million bu", "qty_factor": _BU[commodity] / 1000,
                "bu_per_mt": _BU[commodity], "yield_imp": "bu/ac"}
    if commodity == "Cotton":
        return {"metric": "1,000 MT", "imperial": "1,000 480-lb bales", "qty_factor": None,
                "bu_per_mt": None, "yield_imp": "lb/ac"}
    if commodity == "Rice, Milled":
        return {"metric": "1,000 MT", "imperial": "Million cwt", "qty_factor": LB_PER_MT / 100 / 1000,
                "bu_per_mt": None, "yield_imp": "lb/ac"}
    if commodity.endswith("Meal") or commodity == "Sugar":
        return {"metric": "1,000 MT", "imperial": "1,000 short tons", "qty_factor": 1.10231,
                "bu_per_mt": None, "yield_imp": "lb/ac"}
    return {"metric": "1,000 MT", "imperial": "Million lbs", "qty_factor": LB_PER_MT / 1000,
            "bu_per_mt": None, "yield_imp": "lb/ac"}


def kt_from_raw(value: float | None, unit_id: int | None) -> float | None:
    """Raw PSD quantity -> 1,000 MT (cotton arrives as 1,000 480-lb bales)."""
    if value is None:
        return None
    return value * KG_PER_BALE / 1000 if unit_id == 27 else value


def t_ha_from_raw(value: float | None, unit_id: int | None) -> float | None:
    """Raw PSD yield -> MT/ha (cotton arrives as KG/HA)."""
    if value is None:
        return None
    return value / 1000 if unit_id == 29 else value


def qty_out(commodity: str, kt: float | None, system: str) -> float | None:
    """1,000 MT -> display unit (linear, so it also converts differences)."""
    if kt is None:
        return None
    if system == "metric":
        return kt
    if commodity == "Cotton":
        return kt * 1000 / KG_PER_BALE
    return kt * imperial_spec(commodity)["qty_factor"]


def area_out(kha: float | None, system: str) -> float | None:
    """1,000 ha -> million ha / million acres."""
    if kha is None:
        return None
    return kha / 1000 if system == "metric" else kha * ACRES_PER_HA / 1000


def yield_out(commodity: str, t_ha: float | None, system: str) -> float | None:
    """MT/ha -> MT/ha, bu/ac or lb/ac."""
    if t_ha is None:
        return None
    if system == "metric":
        return t_ha
    bu = imperial_spec(commodity)["bu_per_mt"]
    return t_ha * (bu if bu else LB_PER_MT) / ACRES_PER_HA


def unit_labels(commodity: str, system: str) -> dict:
    spec = imperial_spec(commodity)
    if system == "metric":
        return {"qty": "1,000 MT", "area": "Million ha", "yield": "MT/ha"}
    return {"qty": spec["imperial"], "area": "Million acres", "yield": spec["yield_imp"]}
