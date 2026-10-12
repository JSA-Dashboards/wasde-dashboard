"""
Massive futures API helper for the WASDE dashboard.

Fetches the front-month settlement price for CBOT/CME grains.
Grains quote in ¢/bu — divide by 100 to get $/bu.
Cotton (ICE) and Rice (delisted ZR) are not on Massive; returns None.
"""

from __future__ import annotations

import re
import os
from datetime import date, timedelta
import requests
import streamlit as st

MASSIVE_BASE = "https://api.massive.com/futures/v1"

# WASDE commodity → Massive product code + divisor
# divisor converts Massive quote units to $/bu (or $/lb for oil)
_COMM_MAP = {
    "Corn":         ("ZC", 100.0),   # ¢/bu → $/bu
    "Soybeans":     ("ZS", 100.0),   # ¢/bu → $/bu
    "Wheat":        ("ZW", 100.0),   # ¢/bu → $/bu (Chicago/SRW)
    "Soybean Oil":  ("ZL", 1.0),     # ¢/lb (keep as-is, shown as ¢/lb)
    # Cotton = ICE, Rice = delisted → not available
}

_OUTRIGHT_RE = re.compile(r"^([A-Z]{2,3})([FGHJKMNQUVXZ])(\d)$")


def _secret(key: str) -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")


@st.cache_resource
def _session() -> requests.Session:
    s = requests.Session()
    key = _secret("MASSIVE_API_KEY")
    if key:
        s.headers["Authorization"] = f"Bearer {key}"
    s.headers["User-Agent"] = "JPSI-WASDE/1.0"
    return s


@st.cache_data(ttl=900, show_spinner=False)
def front_month_price(commodity: str) -> tuple[float | None, str | None]:
    """
    Return (price_in_native_units, ticker) for the nearest front-month contract.
    Grains → ¢/bu, Soybean Oil → ¢/lb. Returns (None, None) if not available.
    """
    if commodity not in _COMM_MAP:
        return None, None

    product_code, _ = _COMM_MAP[commodity]
    key = _secret("MASSIVE_API_KEY")
    if not key:
        return None, None

    try:
        # /contracts only lists a date Massive has published: step back from today
        contracts = []
        for back in range(6):
            day = (date.today() - timedelta(days=back)).isoformat()
            r = _session().get(
                f"{MASSIVE_BASE}/contracts",
                params={"product_code": product_code, "active": "true",
                        "date": day, "limit": 400},
                timeout=15,
            )
            r.raise_for_status()
            contracts = r.json().get("results", [])
            if contracts:
                break

        # Keep only single-month outrights not yet expired, nearest first
        today = date.today().isoformat()
        outrights = []
        for c in contracts:
            ticker = c.get("ticker", "")
            if _OUTRIGHT_RE.match(ticker):
                exp = c.get("settlement_date") or c.get("last_trade_date") or ""
                if exp and exp >= today:
                    outrights.append((exp, ticker))

        if not outrights:
            return None, None

        outrights.sort()
        front_ticker = outrights[0][1]

        # Fetch snapshot for front month
        r2 = _session().get(
            f"{MASSIVE_BASE}/snapshot",
            params={"ticker.any_of": front_ticker},
            timeout=15,
        )
        r2.raise_for_status()
        results = r2.json().get("results", [])
        if not results:
            return None, None

        snap = results[0]
        # Prefer settlement; fall back to last trade
        price = (
            (snap.get("session") or {}).get("settlement_price")
            or (snap.get("session") or {}).get("close")
            or (snap.get("last_trade") or {}).get("price")
        )
        if price is None:
            return None, None

        return float(price), front_ticker

    except Exception:
        return None, None


def fmt_futures(commodity: str, price: float | None, ticker: str | None) -> tuple[str, str]:
    """Return (display_value, label) for the futures KPI."""
    if price is None or ticker is None:
        return "N/A", "Front Month"

    _, divisor = _COMM_MAP.get(commodity, ("", 1.0))
    if commodity == "Soybean Oil":
        return f"{price:.2f}¢", f"Futures ({ticker})"
    else:
        dol = price / divisor
        return f"${dol:.2f}", f"Futures ({ticker})"
