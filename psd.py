"""USDA FAS PSD country series via the CompositeVisualization API (reachable from Streamlit Cloud)."""

import re

import pandas as pd
import requests
import streamlit as st

PSD_VIZ = "https://apps.fas.usda.gov/PSDOnlineApi/api/CompositeVisualization"

COMM_CODES = {
    "Soybeans": "2222000", "Corn": "0440000", "Wheat": "0410000",
    "Barley": "0430000", "Canola": "2226000",
}
COUNTRY_CODES = {"Ukraine": "UP", "Russia": "RS", "Australia": "AS", "EU": "E4", "Canada": "CA", "Mexico": "MX", "United States": "US"}
ATTR = {"area_kha": "4", "production_kt": "28", "exports_kt": "88", "ending_stocks_kt": "176", "yield_tha": "184"}


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch(comm_code: str, country_code: str) -> dict:
    """Raises on any failure, so a transient API error is never cached as 'no data'."""
    period = requests.get(f"{PSD_VIZ}/GetReportingPeriods", timeout=20,
                          headers={"User-Agent": "JPSI-WASDE/1.0"}).json()[0]["value"]
    r = requests.post(f"{PSD_VIZ}/GetAllAttributeData", timeout=45,
                      headers={"User-Agent": "JPSI-WASDE/1.0"},
                      json={"reportingPeriod": period, "reportingCutoffPeriod": None,
                            "commodityCode": comm_code, "regionCode": "R00",
                            "countryCode": country_code, "attributeId": "4"})
    r.raise_for_status()
    data = r.json()
    if not data or not data.get("attributeValues"):
        raise ValueError("empty PSD response")
    return data


def country_series(crop: str, country: str) -> pd.DataFrame:
    """USDA PSD marketing-year series for one crop/country: columns start_year, metric, value.

    Response layout: values[i] pairs with headerValues[i+1]; col 1 is the 'Change' column and
    col 2 repeats the current year for the prior WASDE, so both are skipped.
    """
    try:
        data = _fetch(COMM_CODES[crop], COUNTRY_CODES[country])
    except Exception:
        return pd.DataFrame(columns=["start_year", "metric", "value"])
    headers = data.get("headerValues", [])
    rows = []
    for attr in data.get("attributeValues", []):
        aid = str(attr.get("attributeId", "")).strip()
        metric = next((m for m, i in ATTR.items() if i == aid), None)
        if metric is None:
            continue
        vals = attr.get("attributeValues", [])
        for ci, hdr in enumerate(headers[1:]):
            if ci in (1, 2) or ci >= len(vals):
                continue
            m = re.match(r"(\d{2})/\d{2}", str(hdr).strip())
            raw = vals[ci]
            if not m or raw in (None, "", "-"):
                continue
            try:
                rows.append({"start_year": 2000 + int(m.group(1)), "metric": metric,
                             "value": float(str(raw).split("(")[0].replace(",", "").strip())})
            except ValueError:
                continue
    return pd.DataFrame(rows, columns=["start_year", "metric", "value"])
