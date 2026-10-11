"""Unit conversion helpers for USDA PSD data.

PSD stores values in 1000 MT (for grains/oilseeds) or 1000 480-lb bales (cotton).
Display layer converts to million bushels on request.
"""

# 1,000 MT → million bushels (PSD unit × factor = million bu)
_MT_TO_MBU: dict[str, float] = {
    "Corn":         39.368,   # 1 MT = 39.368 bu
    "Soybeans":     36.744,
    "Wheat":        36.744,
    "Rice, Milled": 45.929,   # rough milled equivalent
    "Soybean Oil":  None,     # stays in MT (liquid)
    "Cotton":       None,     # stays in 1000 bales
}

UNIT_LABELS = {
    "metric": {
        "grain": "1,000 MT",
        "cotton": "1,000 480-lb bales",
        "oil": "1,000 MT",
    },
    "imperial": {
        "grain": "Million bu",
        "cotton": "1,000 480-lb bales",
        "oil": "1,000 MT",
    },
}


def convert(value: float | None, commodity: str, to_imperial: bool = False) -> float | None:
    """Convert a PSD value to the requested unit system.

    Returns None unchanged. Commodities without an imperial equivalent
    (oil, cotton) return the value as-is regardless of the flag.
    """
    if value is None:
        return None
    if not to_imperial:
        return value
    factor = _MT_TO_MBU.get(commodity)
    if factor is None:
        return value
    return value * factor / 1000  # 1000 MT × factor bu/MT ÷ 1,000,000 = million bu


def unit_label(commodity: str, to_imperial: bool = False) -> str:
    factor = _MT_TO_MBU.get(commodity)
    if factor is None:
        if commodity == "Cotton":
            return UNIT_LABELS["metric"]["cotton"]
        return UNIT_LABELS["metric"]["oil"]
    return UNIT_LABELS["imperial"]["grain"] if to_imperial else UNIT_LABELS["metric"]["grain"]


def fmt(value: float | None, commodity: str, to_imperial: bool = False) -> str:
    v = convert(value, commodity, to_imperial)
    if v is None:
        return "—"
    if to_imperial and _MT_TO_MBU.get(commodity):
        return f"{v:,.1f}"
    return f"{v:,.0f}"
